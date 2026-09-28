import csv
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.evaluation.synop import ingest_and_verify_synop, normalize_value
from backend.ingestion.synop_previous_runs import fetch_station_previous_runs
from urllib.parse import urlencode


class _Config:
    def __init__(self, root: Path):
        self.root = root
        self.data = {"storage": {"database_path": "db.sqlite3", "export_directory": "exports"}}

    def resolve(self, value: str) -> Path:
        return self.root / value


def _write(path: Path, rows: list[dict]):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)


class SynopVerificationTests(unittest.TestCase):
    def test_previous_run_batch_resumes_from_deterministic_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); raw=root/"raw"; raw.mkdir()
            params={"latitude":12.966667,"longitude":77.583333,"hourly":"temperature_2m_previous_day1,wind_speed_10m_previous_day1","start_date":"2026-08-30","end_date":"2026-08-31","timezone":"UTC","wind_speed_unit":"ms","models":"ncep_gfs_seamless"}
            url="https://example.test?"+urlencode(params); digest=hashlib.sha256(url.encode()).hexdigest()
            times=[f"2026-08-{day:02d}T{hour:02d}:00" for day in (30,31) for hour in range(24)]
            payload={"latitude":12.945007,"longitude":77.578125,"hourly_units":{"temperature_2m_previous_day1":"°C","wind_speed_10m_previous_day1":"m/s"},"hourly":{"time":times,"temperature_2m_previous_day1":[25]*48,"wind_speed_10m_previous_day1":[4]*48}}
            (raw/f"gfs-2026-08-30-2026-08-31-{digest}.json").write_text(json.dumps(payload),encoding="utf-8")
            result=fetch_station_previous_runs(station_id="43295",latitude=12.966667,longitude=77.583333,start="2026-08-30",end="2026-08-31",sources=[{"id":"gfs","api_model":"ncep_gfs_seamless"}],output_directory=root,endpoint="https://example.test",user_agent="test",batch_days=2,resume=True)
            self.assertEqual(result["rows"],96)
            self.assertEqual(result["requests"][0]["retrieval"],"cache")

    def test_units_are_normalized_and_quality_checked(self):
        self.assertAlmostEqual(normalize_value("temperature_2m", "300", "K"), 26.85, places=2)
        self.assertAlmostEqual(normalize_value("wind_speed_10m", "36", "km/h"), 10.0)
        with self.assertRaises(ValueError): normalize_value("wind_speed_10m", "-1", "m/s")

    def test_exact_station_time_matching_missing_values_and_no_future_issue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); observations = root / "obs.csv"; forecasts = root / "fc.csv"
            _write(observations, [{"station_id":"43295","observation_time_utc":"2026-08-31T12:00:00Z","latitude":"12.966667","longitude":"77.583333","air_temperature_c":"25","wind_speed_ms":"",
                                  }, {"station_id":"43295","observation_time_utc":"2026-08-31T15:00:00Z","latitude":"12.966667","longitude":"77.583333","air_temperature_c":"24","wind_speed_ms":"4"}])
            base = {"source_id":"gfs","requested_model_id":"ncep_gfs_seamless","latitude":"12.966667","longitude":"77.583333","issue_time_utc":"2026-08-31T06:00:00Z","valid_time_utc":"2026-08-31T12:00:00Z"}
            _write(forecasts, [{**base,"variable_id":"temperature_2m","value":"298.15","unit":"K"},
                               {**base,"variable_id":"wind_speed_10m","value":"18","unit":"km/h"},
                               {**base,"source_id":"ifs_hres","variable_id":"temperature_2m","value":"99","unit":"°C","issue_time_utc":"2026-08-31T13:00:00Z"}])
            result = ingest_and_verify_synop(_Config(root), observations, forecasts)
            self.assertEqual(result["matched_pairs"], 1)
            connection = sqlite3.connect(root / "db.sqlite3")
            try:
                matched = connection.execute("SELECT observation_value,forecast_value,unit,lead_hours FROM synop_forecast_pairs WHERE match_status='matched'").fetchone()
                self.assertEqual(matched, (25.0, 25.0, "°C", 6.0))
                statuses = dict(connection.execute("SELECT match_status,COUNT(*) FROM synop_forecast_pairs GROUP BY match_status"))
                self.assertGreater(statuses["no_station_requested_forecast"], 0)
                self.assertGreater(statuses["missing_observation"], 0)
            finally:
                connection.close()

    def test_nearby_district_value_is_not_used_as_station_forecast(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); observations = root / "obs.csv"; forecasts = root / "fc.csv"
            _write(observations, [{"station_id":"43295","observation_time_utc":"2026-08-31T12:00:00Z","latitude":"12.966667","longitude":"77.583333","air_temperature_c":"25","wind_speed_ms":"4"}])
            _write(forecasts, [{"source_id":"gfs","requested_model_id":"ncep_gfs_seamless","latitude":"13.05","longitude":"77.60","issue_time_utc":"2026-08-31T06:00:00Z","valid_time_utc":"2026-08-31T12:00:00Z","variable_id":"temperature_2m","value":"25","unit":"°C"}])
            result = ingest_and_verify_synop(_Config(root), observations, forecasts)
            self.assertEqual(result["matched_pairs"], 0)

    def test_missing_forecast_value_is_recorded_not_parsed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); observations=root/"obs.csv"; forecasts=root/"fc.csv"
            _write(observations,[{"station_id":"43295","observation_time_utc":"2026-08-31T12:00:00Z","latitude":"12.966667","longitude":"77.583333","air_temperature_c":"25","wind_speed_ms":"4"}])
            _write(forecasts,[{"source_id":"gfs","requested_model_id":"ncep_gfs_seamless","latitude":"12.966667","longitude":"77.583333","issue_time_utc":"2026-08-31T06:00:00Z","valid_time_utc":"2026-08-31T12:00:00Z","variable_id":"temperature_2m","value":"","unit":"°C"}])
            result=ingest_and_verify_synop(_Config(root),observations,forecasts)
            self.assertEqual(result["matched_pairs"],0)
            connection=sqlite3.connect(root/"db.sqlite3")
            try: self.assertEqual(connection.execute("SELECT match_status FROM synop_forecast_pairs WHERE source_id='gfs' AND variable_id='temperature_2m'").fetchone()[0],"missing_forecast")
            finally: connection.close()

    def test_station_requested_forecast_accepts_snapped_grid_and_records_distance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); observations = root / "obs.csv"; forecasts = root / "fc.csv"
            _write(observations, [{"station_id":"43295","observation_time_utc":"2026-08-31T12:00:00Z","latitude":"12.966667","longitude":"77.583333","air_temperature_c":"25","wind_speed_ms":"4"}])
            _write(forecasts, [{"source_id":"aifs","requested_model_id":"ecmwf_aifs025_single","requested_latitude":"12.966667","requested_longitude":"77.583333","returned_grid_latitude":"13.0","returned_grid_longitude":"77.5","latitude":"13.0","longitude":"77.5","issue_time_utc":"","issue_time_status":"unverified_exact_previous_day1_product","valid_time_utc":"2026-08-31T12:00:00Z","variable_id":"temperature_2m","value":"25","unit":"°C","request_url_sha256":"abc"}])
            result = ingest_and_verify_synop(_Config(root), observations, forecasts)
            self.assertEqual(result["matched_pairs"], 1)
            connection = sqlite3.connect(root / "db.sqlite3")
            try:
                status, distance = connection.execute("SELECT match_status,spatial_distance_km FROM synop_forecast_pairs WHERE source_id='aifs' AND variable_id='temperature_2m'").fetchone()
                self.assertEqual(status, "matched_issue_time_unverified")
                self.assertGreater(distance, 9.0)
            finally: connection.close()

    def test_common_three_source_metrics_use_identical_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); observations=root/"obs.csv"; forecasts=root/"fc.csv"
            _write(observations,[{"station_id":"43295","observation_time_utc":time,"latitude":"12.966667","longitude":"77.583333","air_temperature_c":"25","wind_speed_ms":""} for time in ("2026-08-31T12:00:00Z","2026-08-31T15:00:00Z")])
            base={"requested_model_id":"model","latitude":"12.966667","longitude":"77.583333","issue_time_utc":"2026-08-31T06:00:00Z","variable_id":"temperature_2m","value":"26","unit":"°C"}
            rows=[{**base,"source_id":source,"valid_time_utc":"2026-08-31T12:00:00Z"} for source in ("gfs","ifs_hres","aifs")]
            rows.append({**base,"source_id":"gfs","valid_time_utc":"2026-08-31T15:00:00Z"});_write(forecasts,rows)
            ingest_and_verify_synop(_Config(root),observations,forecasts)
            with (root/"exports"/"synop"/"synop_station_metrics.csv").open(encoding="utf-8") as handle:
                metrics=list(csv.DictReader(handle))
            common=[row for row in metrics if row["scope"]=="common_three_source"]
            full_gfs=[row for row in metrics if row["scope"]=="full_availability" and row["source_id"]=="gfs" and row["variable_id"]=="temperature_2m"]
            self.assertEqual({row["n"] for row in common},{"1"})
            self.assertEqual(sum(int(row["n"]) for row in full_gfs),2)


if __name__ == "__main__": unittest.main()
