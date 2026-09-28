from datetime import datetime
from pathlib import Path
import unittest

from backend.ingestion.open_meteo import PointForecast
from backend.inference.pipeline import _aggregate_source, _aggregate_weather, _daily_point_weather, circular_mean_degrees


def point(*, temperatures=None, speeds=None, directions=None, units=None, precipitation=None):
    times = tuple(f"2026-09-26T{hour:02d}:00" for hour in range(24))
    return PointForecast(
        source_id="gfs", requested_model_id="model", latitude=13.0, longitude=77.0,
        times=times, precipitation=tuple(precipitation or [1.0] * 24), response_sha256="sha", raw_path=Path("raw.json"),
        request_url="url", retrieved_at_utc="2026-09-25T00:00:00+00:00", upstream_run_time_utc=None,
        run_identity_status="not_exposed", temperature_2m=tuple(temperatures or [20.0] * 24),
        wind_speed_10m=tuple(speeds or [2.0] * 24), wind_direction_10m=tuple(directions or [0.0] * 24),
        hourly_units=units or {"temperature_2m": "°C", "wind_speed_10m": "m/s", "wind_direction_10m": "°"},
    )


class TemperatureWindContractTests(unittest.TestCase):
    def test_daily_minimum_maximum_and_wind_maximum(self):
        daily = _daily_point_weather(point(temperatures=list(range(24)), speeds=[value / 2 for value in range(24)]), "Asia/Kolkata", 0, 24)
        values = daily[datetime(2026, 9, 26).date()]
        self.assertEqual(values["temperature_2m"]["minimum"], 0)
        self.assertEqual(values["temperature_2m"]["maximum"], 23)
        self.assertEqual(values["wind_speed_10m"]["maximum"], 11.5)

    def test_circular_direction_crosses_north(self):
        result = circular_mean_degrees([350.0, 10.0])
        self.assertTrue(result < 1.0 or result > 359.0)
        self.assertIsNone(circular_mean_degrees([90.0, 270.0]))

    def test_missing_hour_or_field_is_incomplete(self):
        missing = point(temperatures=[20.0] * 23 + [None])
        result = _aggregate_weather(
            (missing,), issue_local=datetime.fromisoformat("2026-09-25T12:00:00+05:30"), lead_days=(1,), minimum_coverage=1.0,
            timezone_name="Asia/Kolkata", accumulation_start_hour=0, target_start_day_offset=1, required_hours=24,
            expected_units={"temperature_2m": "°C", "wind_speed_10m": "m/s", "wind_direction_10m": "°"},
        )[1]
        self.assertEqual(result[("temperature_2m", "maximum")]["status"], "incomplete")
        self.assertIsNone(result[("temperature_2m", "minimum")]["value"])

    def test_wrong_or_absent_unit_is_explicitly_unsupported(self):
        wrong = point(units={"temperature_2m": "°F", "wind_speed_10m": "km/h"})
        result = _aggregate_weather(
            (wrong,), issue_local=datetime.fromisoformat("2026-09-25T12:00:00+05:30"), lead_days=(1,), minimum_coverage=1.0,
            timezone_name="Asia/Kolkata", accumulation_start_hour=0, target_start_day_offset=1, required_hours=24,
            expected_units={"temperature_2m": "°C", "wind_speed_10m": "m/s", "wind_direction_10m": "°"},
        )[1]
        self.assertEqual(result[("temperature_2m", "maximum")]["status"], "unsupported:missing_field_or_unit")
        self.assertEqual(result[("wind_speed_10m", "maximum")]["status"], "unsupported:missing_field_or_unit")

    def test_rainfall_aggregation_is_unchanged(self):
        p = point(precipitation=[1.0] * 24)
        result = _aggregate_source(
            (p,), issue_local=datetime.fromisoformat("2026-09-25T12:00:00+05:30"), lead_days=(1,), minimum_coverage=1.0,
            timezone_name="Asia/Kolkata", accumulation_start_hour=0, target_start_day_offset=1, required_hours=24,
        )[1]
        self.assertEqual(result["value"], 24.0)
        self.assertEqual(result["status"], "complete")


if __name__ == "__main__":
    unittest.main()
