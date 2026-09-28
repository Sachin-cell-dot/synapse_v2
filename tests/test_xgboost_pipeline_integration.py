from contextlib import closing
from datetime import datetime, timedelta, timezone
import copy
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from backend.ingestion.open_meteo import PointForecast
from backend.ingestion.open_meteo_ensemble import EnsemblePointForecast
from backend.inference.pipeline import run_district_cycle
from backend.utils.config import OperationalConfig, load_config


class XGBoostPipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        source = load_config(Path("configs/ps81.karnataka.json"))
        self.temp = tempfile.TemporaryDirectory()
        data = copy.deepcopy(source.data)
        data["storage"]["database_path"] = str(Path(self.temp.name) / "operational.sqlite3")
        data["storage"]["raw_response_directory"] = str(Path(self.temp.name) / "raw")
        data["storage"]["export_directory"] = str(Path(self.temp.name) / "exports")
        data["forecast"]["request_cache_directory"] = str(Path(self.temp.name) / "cache")
        data["geography"]["sampling"].update({"minimum_points": 1, "maximum_points": 1, "minimum_coverage_fraction": 1.0})
        data["ensemble"]["enabled"] = False
        self.config = OperationalConfig(source.path, source.root, data, source.sha256)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def fake_fetch_points(*, source, coordinates, forecast, raw_directory, cache_directory):
        zone = ZoneInfo(forecast["source_timezone"])
        first = datetime.now(timezone.utc).astimezone(zone).date() + timedelta(days=1)
        times = tuple(f"{(first + timedelta(days=day)).isoformat()}T{hour:02d}:00" for day in range(6) for hour in range(24))
        source_value = {"gfs": 1.0, "ifs_hres": 2.0, "aifs": 3.0}[source["id"]]
        return tuple(PointForecast(source["id"], source["api_model"], lat, lon, times, tuple([source_value / 24] * len(times)), "raw", Path("raw.json"), "url", datetime.now(timezone.utc).isoformat(), None, "not_exposed_by_latest_endpoint", temperature_2m=tuple([20.0 + source_value] * len(times)), wind_speed_10m=tuple([2.0 + source_value] * len(times)), wind_direction_10m=tuple([350.0 if source["id"] == "gfs" else 10.0] * len(times)), hourly_units={"temperature_2m": "°C", "wind_speed_10m": "m/s", "wind_direction_10m": "°"}) for lat, lon in coordinates)

    def test_day1_xgboost_and_later_baselines_are_persisted(self):
        loaded = SimpleNamespace(artifact_version="2e070898b34c589b", feature_schema_sha256="schema")
        with patch("backend.inference.pipeline.fetch_points", side_effect=self.fake_fetch_points), patch("backend.inference.pipeline.load_frozen_model", return_value=loaded), patch("backend.inference.pipeline.infer_expected_errors", return_value=({"gfs": 1.0, "ifs_hres": 2.0, "aifs": 4.0}, {"gfs": 4/7, "ifs_hres": 2/7, "aifs": 1/7})):
            result = run_district_cycle(self.config, "Bengaluru Urban")
        with closing(sqlite3.connect(self.config.data["storage"]["database_path"])) as connection:
            rows = connection.execute("SELECT lead_days,method,artifact_version,predicted_errors_json,weights_json,fallback FROM blended_forecasts WHERE cycle_id=? ORDER BY lead_days", (result["cycle_id"],)).fetchall()
            variable = connection.execute("SELECT value,unit,method,blend_eligibility,status FROM variable_blended_forecasts WHERE cycle_id=? AND lead_days=1 AND variable_id='temperature_2m' AND statistic='maximum'", (result["cycle_id"],)).fetchone()
        self.assertEqual(rows[0][1:3], ("xgboost_expected_absolute_error", "2e070898b34c589b"))
        self.assertIn('"gfs":1.0', rows[0][3])
        self.assertIsNone(rows[0][5])
        self.assertTrue(all(row[1] == "equal_weight_baseline" and row[2] is None for row in rows[1:]))
        self.assertEqual(variable, (22.0, "°C", "equal_weight_experimental_forecast_only", "forecast_only_no_statewide_district_observations", "complete"))

    def test_missing_artifact_records_explicit_equal_weight_fallback(self):
        with patch("backend.inference.pipeline.fetch_points", side_effect=self.fake_fetch_points), patch("backend.inference.pipeline.load_frozen_model", side_effect=FileNotFoundError("missing")):
            result = run_district_cycle(self.config, "Bengaluru Urban")
        with closing(sqlite3.connect(self.config.data["storage"]["database_path"])) as connection:
            method, fallback = connection.execute("SELECT method,fallback FROM blended_forecasts WHERE cycle_id=? AND lead_days=1", (result["cycle_id"],)).fetchone()
        self.assertEqual(method, "equal_weight_baseline")
        self.assertEqual(fallback, "equal_weight_baseline_fallback:FileNotFoundError")

    @staticmethod
    def fake_ensemble_points(*, source, coordinates, forecast, raw_directory, cache_directory):
        zone = ZoneInfo(forecast["source_timezone"]); first = datetime.now(timezone.utc).astimezone(zone).date() + timedelta(days=1)
        times = tuple(f"{(first + timedelta(days=day)).isoformat()}T{hour:02d}:00" for day in range(6) for hour in range(24))
        return tuple(EnsemblePointForecast(source["id"], source["api_model"], lat, lon, lat + .1, lon + .1, times, tuple([1/24] * len(times)), tuple([.2] * len(times)), "ensemble-raw", datetime.now(timezone.utc).isoformat()) for lat, lon in coordinates)

    def test_ensemble_is_display_only_and_does_not_change_locked_xgboost(self):
        self.config.data["ensemble"]["enabled"] = True
        loaded = SimpleNamespace(artifact_version="2e070898b34c589b", feature_schema_sha256="schema")
        expected_weights = {"gfs": 4/7, "ifs_hres": 2/7, "aifs": 1/7}
        with patch("backend.inference.pipeline.fetch_points", side_effect=self.fake_fetch_points), patch("backend.inference.pipeline.fetch_ensemble_points", side_effect=self.fake_ensemble_points), patch("backend.inference.pipeline.load_frozen_model", return_value=loaded), patch("backend.inference.pipeline.infer_expected_errors", return_value=({"gfs": 1.0, "ifs_hres": 2.0, "aifs": 4.0}, expected_weights)):
            result = run_district_cycle(self.config, "Bengaluru Urban")
        with closing(sqlite3.connect(self.config.data["storage"]["database_path"])) as connection:
            ensemble = connection.execute("SELECT source_class,blend_role,ensemble_spread_mm,status FROM source_forecasts WHERE cycle_id=? AND source_id='gfs_ens' AND lead_days=1", (result["cycle_id"],)).fetchone()
            blend = connection.execute("SELECT method,artifact_version,weights_json FROM blended_forecasts WHERE cycle_id=? AND lead_days=1", (result["cycle_id"],)).fetchone()
        self.assertEqual(ensemble[:2], ("ensemble", "display_only_not_in_locked_blend"))
        self.assertGreater(ensemble[2], 0)
        self.assertEqual(ensemble[3], "complete")
        self.assertEqual(blend[:2], ("xgboost_expected_absolute_error", "2e070898b34c589b"))
        self.assertNotIn("gfs_ens", blend[2])

    def test_missing_ensemble_does_not_block_locked_xgboost(self):
        self.config.data["ensemble"]["enabled"] = True
        loaded = SimpleNamespace(artifact_version="2e070898b34c589b", feature_schema_sha256="schema")
        with patch("backend.inference.pipeline.fetch_points", side_effect=self.fake_fetch_points), patch("backend.inference.pipeline.fetch_ensemble_points", side_effect=RuntimeError("unavailable")), patch("backend.inference.pipeline.load_frozen_model", return_value=loaded), patch("backend.inference.pipeline.infer_expected_errors", return_value=({"gfs": 1.0, "ifs_hres": 2.0, "aifs": 4.0}, {"gfs": 4/7, "ifs_hres": 2/7, "aifs": 1/7})):
            result = run_district_cycle(self.config, "Bengaluru Urban")
        with closing(sqlite3.connect(self.config.data["storage"]["database_path"])) as connection:
            ensemble_status = connection.execute("SELECT status FROM source_forecasts WHERE cycle_id=? AND source_id='gfs_ens' AND lead_days=1", (result["cycle_id"],)).fetchone()[0]
            method = connection.execute("SELECT method FROM blended_forecasts WHERE cycle_id=? AND lead_days=1", (result["cycle_id"],)).fetchone()[0]
        self.assertEqual(ensemble_status, "unavailable:RuntimeError")
        self.assertEqual(method, "xgboost_expected_absolute_error")


if __name__ == "__main__":
    unittest.main()
