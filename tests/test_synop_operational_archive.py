import copy
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from backend.ingestion.open_meteo import PointForecast
from backend.ingestion.synop_operational import archive_station_forecasts, eligible_archived_forecasts
from backend.utils.config import OperationalConfig, load_config
from backend.utils.store import initialize_database


class SynopOperationalArchiveTests(unittest.TestCase):
    def setUp(self):
        source = load_config(Path("configs/ps81.karnataka.json"))
        self.temp = tempfile.TemporaryDirectory()
        data = copy.deepcopy(source.data)
        data["storage"]["database_path"] = str(Path(self.temp.name) / "archive.sqlite3")
        data["storage"]["raw_response_directory"] = str(Path(self.temp.name) / "raw")
        data["forecast"]["request_cache_directory"] = str(Path(self.temp.name) / "cache")
        self.config = OperationalConfig(source.path, source.root, data, source.sha256)
        self.database = Path(data["storage"]["database_path"])
        initialize_database(self.database)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "INSERT INTO forecast_cycles VALUES(?,?,?,?,?)",
                ("cycle", "2026-09-26T00:00:00+00:00", "config", "experimental", "2026-09-26T00:00:00+00:00"),
            )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def point(source, latitude, longitude):
        return PointForecast(
            source_id=source["id"], requested_model_id=source["api_model"], latitude=latitude, longitude=longitude,
            times=("2026-09-27T00:00", "2026-09-27T01:00"), precipitation=(0.0, 0.0),
            response_sha256=f"hash-{source['id']}", raw_path=Path("unused.json"), request_url="cached-test",
            retrieved_at_utc="2026-09-26T00:01:00+00:00", upstream_run_time_utc=None,
            run_identity_status="not_exposed_by_latest_endpoint", temperature_2m=(20.0, 21.0),
            wind_speed_10m=(2.0, 3.0), hourly_units={"temperature_2m": "°C", "wind_speed_10m": "m/s"},
            requested_at_utc="2026-09-26T00:00:00+00:00", returned_latitude=13.0, returned_longitude=77.625,
        )

    def test_archives_each_source_and_is_idempotent(self):
        fetcher = Mock(side_effect=lambda **kwargs: self.point(kwargs["source"], kwargs["latitude"], kwargs["longitude"]))
        first = archive_station_forecasts(self.config, "cycle", fetcher=fetcher)
        second = archive_station_forecasts(self.config, "cycle", fetcher=fetcher)
        self.assertEqual(first["status"], "complete")
        self.assertEqual(first["complete"], 3)
        self.assertEqual(second["skipped"], 3)
        self.assertEqual(fetcher.call_count, 3)
        with closing(sqlite3.connect(self.database)) as connection:
            request = connection.execute(
                "SELECT requested_latitude,returned_grid_latitude,grid_distance_km,status FROM synop_station_forecast_requests WHERE source_id='gfs'"
            ).fetchone()
            self.assertEqual(request[0:2], (12.966667, 13.0))
            self.assertGreater(request[2], 0)
            self.assertEqual(request[3], "complete")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM synop_station_forecast_values").fetchone()[0], 12)

    def test_one_source_failure_is_recorded_without_changing_cycle(self):
        def fetcher(**kwargs):
            if kwargs["source"]["id"] == "ifs_hres":
                raise RuntimeError("bounded source failure")
            return self.point(kwargs["source"], kwargs["latitude"], kwargs["longitude"])

        result = archive_station_forecasts(self.config, "cycle", fetcher=fetcher)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["failed"], 1)
        with closing(sqlite3.connect(self.database)) as connection:
            statuses = dict(connection.execute("SELECT source_id,status FROM synop_station_forecast_requests"))
            self.assertEqual(statuses, {"gfs": "complete", "ifs_hres": "failed", "aifs": "complete"})
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM forecast_cycles WHERE cycle_id='cycle'").fetchone()[0], 1)

    def test_missing_station_values_are_incomplete_not_complete(self):
        def fetcher(**kwargs):
            point = self.point(kwargs["source"], kwargs["latitude"], kwargs["longitude"])
            return PointForecast(**{**point.__dict__, "wind_speed_10m": (2.0, None)})

        result = archive_station_forecasts(self.config, "cycle", fetcher=fetcher)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["incomplete"], 3)
        with closing(sqlite3.connect(self.database)) as connection:
            statuses = {row[0] for row in connection.execute("SELECT status FROM synop_station_forecast_requests")}
            self.assertEqual(statuses, {"incomplete"})

    def test_matching_excludes_forecasts_archived_after_observation(self):
        archive_station_forecasts(self.config, "cycle", fetcher=lambda **kwargs: self.point(kwargs["source"], kwargs["latitude"], kwargs["longitude"]))
        observation = "2026-09-26T18:30:00+00:00"
        with closing(sqlite3.connect(self.database)) as connection, connection:
            rows = eligible_archived_forecasts(connection, station_id="43295", observation_time_utc=observation)
            self.assertEqual(len(rows), 6)
            connection.execute("UPDATE synop_station_forecast_requests SET request_time_utc=? WHERE source_id='gfs'", (observation,))
            connection.execute("UPDATE synop_station_forecast_values SET request_time_utc=? WHERE source_id='gfs'", (observation,))
            rows = eligible_archived_forecasts(connection, station_id="43295", observation_time_utc=observation)
            self.assertEqual({row["source_id"] for row in rows}, {"aifs", "ifs_hres"})


if __name__ == "__main__":
    unittest.main()
