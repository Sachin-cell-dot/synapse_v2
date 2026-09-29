from datetime import datetime
import unittest

from backend.ingestion.open_meteo_ensemble import EnsemblePointForecast, parse_ensemble_payload
from backend.inference.pipeline import _aggregate_ensemble


class EnsembleContractTests(unittest.TestCase):
    def test_parser_accepts_exact_mean_spread_and_mm_units(self):
        payload = {"hourly_units": {"time": "iso8601", "precipitation": "mm", "precipitation_spread": "mm"}, "hourly": {"time": ["2026-09-27T00:00"], "precipitation": [1.2], "precipitation_spread": [.4]}}
        times, mean, spread = parse_ensemble_payload(payload, mean_variable="precipitation", spread_variable="precipitation_spread", required_unit="mm")
        self.assertEqual((times, mean, spread), (("2026-09-27T00:00",), (1.2,), (.4,)))

    def test_parser_rejects_wrong_or_undefined_spread_units(self):
        payload = {"hourly_units": {"time": "iso8601", "precipitation": "mm", "precipitation_spread": "undefined"}, "hourly": {"time": ["2026-09-27T00:00"], "precipitation": [1.2], "precipitation_spread": [None]}}
        with self.assertRaisesRegex(ValueError, "units"):
            parse_ensemble_payload(payload, mean_variable="precipitation", spread_variable="precipitation_spread", required_unit="mm")

    def test_daily_interval_and_point_coverage_are_required(self):
        times = tuple(f"2026-09-27T{hour:02d}:00" for hour in range(24))
        complete = EnsemblePointForecast("gfs_ens", "ncep_gefs025_ensemble_mean", 13, 77.5, 13, 77.5, times, tuple([1.0] * 24), tuple([.5] * 24), "sha", "time")
        incomplete = EnsemblePointForecast("gfs_ens", "ncep_gefs025_ensemble_mean", 13.1, 77.6, 13, 77.5, times[:-1], tuple([1.0] * 23), tuple([.5] * 23), "sha", "time")
        result = _aggregate_ensemble((complete, incomplete), issue_local=datetime.fromisoformat("2026-09-26T12:00:00+05:30"), lead_days=(1,), minimum_coverage=1.0, timezone_name="Asia/Kolkata", accumulation_start_hour=0, target_start_day_offset=1, required_hours=24)[1]
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["value"])
        self.assertIsNone(result["spread"])


if __name__ == "__main__":
    unittest.main()
