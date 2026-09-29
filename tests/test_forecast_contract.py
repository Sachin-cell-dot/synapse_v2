from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing

from backend.ingestion.open_meteo import PointForecast
from backend.inference.pipeline import _aggregate_source, _daily_point_values
from backend.utils.store import write_cycle


def point(times, values, latitude=13.0):
    return PointForecast(
        source_id="gfs", requested_model_id="ncep_gfs_seamless", latitude=latitude, longitude=77.0,
        times=tuple(times), precipitation=tuple(values), response_sha256="raw-sha", raw_path=Path("raw.json"),
        request_url="https://example.invalid", retrieved_at_utc="2026-09-25T06:00:00+00:00",
        upstream_run_time_utc=None, run_identity_status="not_exposed_by_latest_endpoint",
    )


class ForecastContractTests(unittest.TestCase):
    def setUp(self):
        self.times = [f"2026-09-26T{hour:02d}:00" for hour in range(24)]

    def test_complete_day_requires_24_distinct_aligned_non_null_hours(self):
        complete = _daily_point_values(point(self.times, [1.0] * 24), "Asia/Kolkata", 0, 24)
        self.assertTrue(complete[datetime(2026, 9, 26).date()]["complete"])
        self.assertEqual(complete[datetime(2026, 9, 26).date()]["value"], 24.0)

        duplicate_times = self.times[:-1] + [self.times[-2]]
        duplicate = _daily_point_values(point(duplicate_times, [1.0] * 24), "Asia/Kolkata", 0, 24)
        self.assertFalse(duplicate[datetime(2026, 9, 26).date()]["complete"])

        missing = _daily_point_values(point(self.times, [1.0] * 23 + [None]), "Asia/Kolkata", 0, 24)
        self.assertFalse(missing[datetime(2026, 9, 26).date()]["complete"])

    def test_lead_one_defaults_to_next_complete_ist_day_and_enforces_points(self):
        issue = datetime.fromisoformat("2026-09-25T12:00:00+05:30")
        complete_point = point(self.times, [1.0] * 24)
        incomplete_point = point(self.times[:-1], [1.0] * 23, latitude=14.0)
        result = _aggregate_source(
            (complete_point, incomplete_point), issue_local=issue, lead_days=(1,), minimum_coverage=1.0,
            timezone_name="Asia/Kolkata", accumulation_start_hour=0, target_start_day_offset=1, required_hours=24,
        )[1]
        self.assertIsNone(result["value"])
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["complete_point_count"], 1)
        self.assertEqual(result["required_point_count"], 2)

    def test_source_provenance_contract_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "forecast.sqlite3"
            source = {
                "cycle_id": "cycle", "district_id": "495", "source_id": "gfs", "requested_model_id": "ncep_gfs_seamless",
                "retrieved_at_utc": "2026-09-25T06:01:00+00:00", "issue_time_utc": "2026-09-25T06:00:00+00:00",
                "upstream_run_time_utc": None, "run_identity_status": "not_exposed_by_latest_endpoint_stitched_data_possible",
                "valid_start_utc": "2026-09-25T18:30:00+00:00", "valid_end_utc": "2026-09-26T18:30:00+00:00",
                "lead_days": 1, "unit": "mm", "precipitation_mm": 24.0, "hourly_completeness": 1.0,
                "complete_point_count": 5, "required_point_count": 4, "point_coverage": 1.0, "status": "complete",
                "raw_response_sha256": "raw-set-sha",
            }
            write_cycle(
                path=database,
                cycle={"cycle_id": "cycle", "retrieved_at_utc": "2026-09-25T06:01:00+00:00", "configuration_sha256": "config", "mode": "test", "created_at_utc": "2026-09-25T06:01:00+00:00"},
                source_rows=[source], blend_rows=[{
                    "cycle_id": "cycle", "district_id": "495", "valid_start_utc": "2026-09-25T18:30:00+00:00", "valid_end_utc": "2026-09-26T18:30:00+00:00",
                    "lead_days": 1, "forecast_mm": 24.0, "status": "complete", "fallback": None,
                    "method": "xgboost_expected_absolute_error", "predicted_errors": {"gfs": 1.0}, "weights": {"gfs": 1.0},
                    "artifact_version": "version", "feature_schema_sha256": "schema", "issued_at_utc": "2026-09-25T06:00:00+00:00",
                }],
            )
            with closing(sqlite3.connect(database)) as connection:
                saved = connection.execute("SELECT requested_model_id,retrieved_at_utc,issue_time_utc,upstream_run_time_utc,unit,hourly_completeness,point_coverage,status FROM source_forecasts").fetchone()
            self.assertEqual(saved, ("ncep_gfs_seamless", "2026-09-25T06:01:00+00:00", "2026-09-25T06:00:00+00:00", None, "mm", 1.0, 1.0, "complete"))
            with closing(sqlite3.connect(database)) as connection:
                blend = connection.execute("SELECT method,predicted_errors_json,weights_json,artifact_version,feature_schema_sha256,fallback FROM blended_forecasts").fetchone()
            self.assertEqual(blend, ("xgboost_expected_absolute_error", '{"gfs":1.0}', '{"gfs":1.0}', "version", "schema", None))


if __name__ == "__main__":
    unittest.main()
