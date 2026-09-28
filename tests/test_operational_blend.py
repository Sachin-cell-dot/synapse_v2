import unittest

from backend.inference.blend import blend_forecasts


class BlendTests(unittest.TestCase):
    def test_inverse_mae_prefers_lower_error(self):
        result = blend_forecasts(
            {"alpha": 10.0, "beta": 20.0, "gamma": 30.0},
            {"alpha": [1.0, 1.0], "beta": [2.0, 2.0], "gamma": [4.0, 4.0]},
            power=2.0,
            mae_floor=0.1,
            minimum_sources=2,
        )
        self.assertEqual(result.status, "complete")
        self.assertGreater(result.weights["alpha"], result.weights["beta"])
        self.assertGreater(result.weights["beta"], result.weights["gamma"])
        self.assertAlmostEqual(sum(result.weights.values()), 1.0)

    def test_missing_source_is_degraded_and_renormalized(self):
        result = blend_forecasts(
            {"alpha": 10.0, "beta": None, "gamma": 30.0},
            {"alpha": [1.0], "gamma": [1.0]},
            power=2.0,
            mae_floor=0.1,
            minimum_sources=2,
        )
        self.assertEqual(result.status, "degraded")
        self.assertAlmostEqual(result.forecast_mm, 20.0)
        self.assertAlmostEqual(sum(result.weights.values()), 1.0)

    def test_no_history_uses_equal_weights(self):
        result = blend_forecasts(
            {"alpha": 3.0, "beta": 9.0}, {}, power=2.0, mae_floor=0.1, minimum_sources=2
        )
        self.assertEqual(result.fallback, "equal_weight_no_complete_history")
        self.assertAlmostEqual(result.forecast_mm, 6.0)

    def test_does_not_publish_below_minimum_sources(self):
        result = blend_forecasts(
            {"alpha": 3.0, "beta": None}, {"alpha": [1.0]}, power=2.0, mae_floor=0.1, minimum_sources=2
        )
        self.assertEqual(result.status, "insufficient_sources")
        self.assertIsNone(result.forecast_mm)


if __name__ == "__main__":
    unittest.main()
