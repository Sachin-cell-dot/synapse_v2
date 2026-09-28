import csv
from pathlib import Path
import tempfile
import unittest

from backend.inference.blend import blend_forecasts
from backend.models.xgboost_blend import expected_error_weights
from backend.training.xgboost_blend import build_rows, train_model
from backend.utils.config import load_config


class XGBoostBlendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config(Path("configs/ps81.karnataka.json"))
        cls.fixture_directory = tempfile.TemporaryDirectory()
        fixture_path = Path(cls.fixture_directory.name) / "aligned_history.csv"
        fieldnames = [
            "date", "district_code", "imd_actual_mm",
            "gfs_rain_mm", "ifs_hres_rain_mm", "aifs_rain_mm",
            "gfs_coverage_status", "ifs_hres_coverage_status", "aifs_coverage_status",
        ]
        with fixture_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for day in ("2025-10-01", "2026-01-01"):
                writer.writerow({
                    "date": day, "district_code": "495", "imd_actual_mm": "2.0",
                    "gfs_rain_mm": "1.0", "ifs_hres_rain_mm": "2.0", "aifs_rain_mm": "3.0",
                    "gfs_coverage_status": "pass", "ifs_hres_coverage_status": "pass", "aifs_coverage_status": "pass",
                })
        cls.config.data["historical_bootstrap"]["path"] = str(fixture_path)
        cls.train, cls.validation, cls.contract = build_rows(cls.config)

    @classmethod
    def tearDownClass(cls):
        cls.fixture_directory.cleanup()

    def test_chronological_split_does_not_include_held_out_dates(self):
        self.assertTrue(all("2025-10-01" <= row["date"] <= "2025-12-31" for row in self.train))
        self.assertTrue(all("2026-01-01" <= row["date"] <= "2026-04-30" for row in self.validation))
        self.assertFalse(any(row["date"] >= "2026-05-01" for row in self.train + self.validation))

    def test_feature_schema_contains_no_observation_or_target_fields(self):
        forbidden = ("actual", "observ", "target", "error", "verification", "imd")
        self.assertFalse(any(any(token in feature.casefold() for token in forbidden) for feature in self.contract["feature_names"]))
        self.assertEqual(self.contract["historical_run_identity"], "unknown_legacy_daily_forecast_alignment_not_exact_run")

    def test_each_aligned_district_day_has_exactly_three_source_rows(self):
        counts = {}
        for row in self.train + self.validation:
            key = (row["date"], row["district_id"])
            counts.setdefault(key, set()).add(row["source_id"])
        self.assertTrue(counts)
        self.assertTrue(all(sources == {"gfs", "ifs_hres", "aifs"} for sources in counts.values()))

    def test_inverse_predicted_error_weights_sum_to_one(self):
        weights = expected_error_weights({"gfs": 1.0, "ifs_hres": 2.0, "aifs": 4.0}, 0.1)
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertGreater(weights["gfs"], weights["ifs_hres"])
        self.assertGreater(weights["ifs_hres"], weights["aifs"])

    def test_named_baselines_remain_available_for_fallback(self):
        inverse = blend_forecasts({"gfs": 1.0, "ifs_hres": 2.0, "aifs": 3.0}, {"gfs": [1.0], "ifs_hres": [2.0], "aifs": [3.0]}, power=2.0, mae_floor=0.1, minimum_sources=2)
        equal = blend_forecasts({"gfs": 1.0, "ifs_hres": 2.0, "aifs": 3.0}, {}, power=2.0, mae_floor=0.1, minimum_sources=2)
        self.assertIsNone(inverse.fallback)
        self.assertEqual(equal.fallback, "equal_weight_no_complete_history")

    def test_frozen_configuration_refuses_retraining(self):
        with self.assertRaisesRegex(RuntimeError, "training is frozen"):
            train_model(self.config)


if __name__ == "__main__":
    unittest.main()
