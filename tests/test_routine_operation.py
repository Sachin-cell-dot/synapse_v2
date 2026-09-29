import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from backend.operations.routine import run_routine
from backend.utils.config import OperationalConfig, load_config
from backend.utils.store import initialize_database


NOW = datetime(2026, 9, 26, 2, 30, tzinfo=timezone.utc)


class RoutineOperationTests(unittest.TestCase):
    def setUp(self):
        source = load_config(Path("configs/ps81.karnataka.json"))
        self.temp = tempfile.TemporaryDirectory()
        data = copy.deepcopy(source.data)
        data["storage"]["database_path"] = str(Path(self.temp.name) / "routine.sqlite3")
        data["storage"]["export_directory"] = str(Path(self.temp.name) / "exports")
        data["routine"]["health_log_path"] = str(Path(self.temp.name) / "health.jsonl")
        self.config = OperationalConfig(source.path, source.root, data, source.sha256)

    def tearDown(self):
        self.temp.cleanup()

    @property
    def database(self):
        return Path(self.config.data["storage"]["database_path"])

    @property
    def health(self):
        return Path(self.config.data["routine"]["health_log_path"])

    def add_statewide_cycle(self):
        initialize_database(self.database)
        with sqlite3.connect(self.database) as connection:
            connection.execute("insert into forecast_cycles values(?,?,?,?,?)", ("existing", "2026-09-26T02:00:00+00:00", "config", "experimental", "2026-09-26T02:00:00+00:00"))
            for district in range(31):
                connection.execute("insert into blended_forecasts values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("existing", str(district), "2026-09-26T18:30:00+00:00", "2026-09-27T18:30:00+00:00", 1, 0.0, "complete", None, "test", "{}", None, None, "{}", "2026-09-26T02:00:00+00:00"))

    def test_dry_run_is_no_write_and_reports_duplicate(self):
        self.add_statewide_cycle()
        runner = Mock()
        station_archive = Mock()
        result = run_routine(self.config, dry_run=True, now_utc=NOW, run_forecast=runner, archive_stations=station_archive)
        self.assertEqual(result["duplicate_cycle_id"], "existing")
        self.assertEqual(result["synop_station_count"], 1)
        self.assertFalse(self.health.exists())
        runner.assert_not_called()
        station_archive.assert_not_called()

    def test_duplicate_actual_invocation_is_skipped(self):
        self.add_statewide_cycle()
        runner = Mock()
        result = run_routine(self.config, now_utc=NOW, run_forecast=runner)
        self.assertEqual(result["status"], "skipped_duplicate")
        runner.assert_not_called()
        self.assertEqual(json.loads(self.health.read_text())["cycle_id"], "existing")

    def test_unavailable_source_fails_before_issuance_and_logs(self):
        def unavailable(_config):
            raise RuntimeError("source=gfs district_batch=492 request unavailable")
        with self.assertRaises(RuntimeError):
            run_routine(self.config, now_utc=NOW, run_forecast=unavailable)
        self.assertEqual(json.loads(self.health.read_text())["status"], "failed_before_or_during_issuance")
        self.assertFalse(self.database.exists())

    def test_unavailable_imd_does_not_invalidate_issued_cycle(self):
        forecast = lambda _config: {"cycle_id": "new", "districts": 31, "blend_rows": 186}
        exported = lambda _config, _cycle: {"status": "pass", "rows": 186, "path": "export.csv"}
        def unavailable_imd(_config, _date):
            raise RuntimeError("IMD product not published")
        station_archive = Mock(return_value={"status": "partial", "complete": 2, "failed": 1})
        result = run_routine(self.config, now_utc=NOW, run_forecast=forecast, export_forecast=exported, fetch_imd=unavailable_imd, archive_stations=station_archive)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["imd"]["status"], "unavailable")
        self.assertEqual(result["synop_archive"]["status"], "partial")
        station_archive.assert_called_once_with(self.config, "new")
        self.assertEqual(json.loads(self.health.read_text())["imd"]["error"], "IMD product not published")


if __name__ == "__main__":
    unittest.main()
