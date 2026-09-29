from datetime import datetime, timezone
import unittest

from backend.evaluation.xgboost_holdout import continuous_metrics, event_metrics, prior_inverse_mae_weights


class FrozenHoldoutTests(unittest.TestCase):
    def test_paired_denominator_is_enforced(self):
        with self.assertRaises(ValueError):
            continuous_metrics([1.0, 2.0], [1.0], 2)

    def test_continuous_metric_calculations(self):
        result = continuous_metrics([1.0, 3.0], [2.0, 1.0], 4)
        self.assertEqual(result["n"], 2)
        self.assertEqual(result["coverage"], 0.5)
        self.assertAlmostEqual(result["mae_mm"], 1.5)
        self.assertAlmostEqual(result["rmse_mm"], (2.5) ** 0.5)
        self.assertAlmostEqual(result["bias_mm"], -0.5)

    def test_event_metrics_and_unavailable_denominators(self):
        result = event_metrics([0.0, 10.0, 20.0], [0.0, 5.0, 25.0], 10.0)
        self.assertEqual((result["hits"], result["misses"], result["false_alarms"]), (1, 1, 0))
        self.assertEqual(result["pod"], 0.5)
        self.assertEqual(result["false_alarm_ratio"], 0.0)
        self.assertEqual(result["csi"], 0.5)
        unavailable = event_metrics([0.0], [0.0], 10.0)
        self.assertIsNone(unavailable["pod"])
        self.assertIsNone(unavailable["false_alarm_ratio"])
        self.assertIsNone(unavailable["csi"])

    def test_inverse_mae_uses_only_strictly_prior_available_observations(self):
        issue = datetime(2026, 5, 10, tzinfo=timezone.utc)
        history = [
            {"date": "2026-05-01", "district_id": "1", "verification_available_at": datetime(2026, 5, 9, tzinfo=timezone.utc), "actual_mm": 0.0, "forecasts": {"gfs": 1.0, "ifs_hres": 2.0, "aifs": 3.0}},
            {"date": "2026-05-02", "district_id": "1", "verification_available_at": issue, "actual_mm": 100.0, "forecasts": {"gfs": 0.0, "ifs_hres": 0.0, "aifs": 0.0}},
            {"date": "2026-05-03", "district_id": "1", "verification_available_at": datetime(2026, 5, 11, tzinfo=timezone.utc), "actual_mm": 100.0, "forecasts": {"gfs": 0.0, "ifs_hres": 0.0, "aifs": 0.0}},
        ]
        weights, status, used = prior_inverse_mae_weights(history, district_id="1", issue_time=issue, source_ids=("gfs", "ifs_hres", "aifs"), window=60, power=2.0, floor=0.1)
        self.assertEqual(used, 1)
        self.assertEqual(status, "inverse_mae_past_available_only")
        self.assertGreater(weights["gfs"], weights["ifs_hres"])

    def test_no_available_history_has_named_equal_fallback(self):
        weights, status, used = prior_inverse_mae_weights([], district_id="1", issue_time=datetime(2026, 5, 1, tzinfo=timezone.utc), source_ids=("gfs", "ifs_hres", "aifs"), window=60, power=2.0, floor=0.1)
        self.assertEqual(status, "equal_weight_no_available_history")
        self.assertEqual(used, 0)
        self.assertAlmostEqual(sum(weights.values()), 1.0)


if __name__ == "__main__":
    unittest.main()
