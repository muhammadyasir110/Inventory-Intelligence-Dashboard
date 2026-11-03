import _testenv  # noqa: F401  (isolated test database; must be first)
import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from analytics.forecasting_engine import (
    simple_moving_average,
    weighted_moving_average,
    exponential_smoothing,
    holt_linear_smoothing,
    calculate_forecast_errors
)


class TestForecastingEngine(unittest.TestCase):

    def setUp(self):
        self.series = [10.0, 12.0, 14.0, 13.0, 15.0, 16.0, 18.0, 17.0, 19.0, 20.0]

    def test_sma(self):
        sma_vals = simple_moving_average(self.series, window=3)
        self.assertEqual(len(sma_vals), len(self.series))
        self.assertGreater(sma_vals[-1], 15.0)

    def test_wma(self):
        wma_vals = weighted_moving_average(self.series, weights=[1.0, 2.0, 3.0])
        self.assertEqual(len(wma_vals), len(self.series))
        self.assertGreater(wma_vals[-1], 15.0)

    def test_ses(self):
        ses_vals = exponential_smoothing(self.series, alpha=0.3)
        self.assertEqual(len(ses_vals), len(self.series))
        self.assertGreater(ses_vals[-1], 10.0)

    def test_holt_linear(self):
        res = holt_linear_smoothing(self.series, alpha=0.3, beta=0.1, horizons=[7, 30])
        self.assertIn("forecasts", res)
        self.assertIn("7", res["forecasts"])
        self.assertIn("30", res["forecasts"])
        self.assertGreater(res["forecasts"]["7"], 0.0)

    def test_forecast_metrics(self):
        actual = [10.0, 12.0, 14.0, 16.0, 18.0]
        pred = [10.0, 11.0, 15.0, 16.0, 20.0]
        metrics = calculate_forecast_errors(actual, pred)
        self.assertIn("mae", metrics)
        self.assertIn("rmse", metrics)
        self.assertIn("mape", metrics)
        self.assertGreaterEqual(metrics["mae"], 0.0)
        self.assertGreaterEqual(metrics["mape"], 0.0)


if __name__ == "__main__":
    unittest.main()
