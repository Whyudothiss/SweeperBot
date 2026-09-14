import unittest

import numpy as np
import pandas as pd

from strategy_engine import StrategyConfig, detect_swings, generate_signals, simulate_trade


def candles(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["datetime_utc", "open", "high", "low", "close"]).assign(
        datetime_utc=lambda frame: pd.to_datetime(frame["datetime_utc"], utc=True),
        is_suspect=False,
    )


class StrategyEngineTests(unittest.TestCase):
    def test_bos_uses_intrabar_touch_not_signal_candle_close(self) -> None:
        signal_df = candles([
            ("2025-01-01 00:00", 9.6, 9.8, 9.5, 9.7),
            ("2025-01-01 00:05", 9.5, 9.7, 9.0, 9.4),
            ("2025-01-01 00:10", 9.5, 10.2, 9.4, 10.0),
            ("2025-01-01 00:15", 9.5, 9.8, 8.8, 9.4),
            # High breaks 10.2, but close does not: this must still trigger.
            ("2025-01-01 00:20", 10.0, 10.3, 9.9, 10.1),
            ("2025-01-01 00:25", 10.1, 10.4, 10.0, 10.3),
        ])
        config = StrategyConfig(lookback=1, lookforward=1)
        signals = generate_signals(detect_swings(signal_df, 1, 1), config)

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["direction"], "long")
        self.assertEqual(signals[0]["entry_price"], 10.2)
        self.assertEqual(signals[0]["fill_index"], 4)

    def test_first_m1_touch_sets_entry_time_and_exact_wick_stop(self) -> None:
        exec_df = candles([
            ("2025-01-01 00:20", 10.0, 10.1, 9.9, 10.0),
            ("2025-01-01 00:21", 10.0, 10.25, 9.95, 10.2),
            ("2025-01-01 00:22", 10.2, 11.3, 10.1, 11.0),
            ("2025-01-01 00:23", 11.0, 11.1, 10.15, 10.3),
        ])
        signal = {
            "time": pd.Timestamp("2025-01-01 00:20", tz="UTC"),
            "trigger_end_time": pd.Timestamp("2025-01-01 00:25", tz="UTC"),
            "direction": "long",
            "entry_price": 10.2,
            "sweep_extreme": 9.2,
        }

        trade = simulate_trade(signal, exec_df, np.nan, 10_000, StrategyConfig())

        self.assertIsNotNone(trade)
        assert trade is not None
        self.assertEqual(trade.entry_time, pd.Timestamp("2025-01-01 00:21", tz="UTC"))
        self.assertEqual(trade.entry_price, 10.2)
        self.assertEqual(trade.initial_stop, 9.2)

    def test_gap_through_bos_fills_at_m1_open(self) -> None:
        exec_df = candles([
            ("2025-01-01 00:20", 10.4, 10.6, 10.3, 10.5),
            ("2025-01-01 00:21", 10.5, 10.6, 9.0, 9.2),
        ])
        signal = {
            "time": pd.Timestamp("2025-01-01 00:20", tz="UTC"),
            "trigger_end_time": pd.Timestamp("2025-01-01 00:21", tz="UTC"),
            "direction": "long",
            "entry_price": 10.2,
            "sweep_extreme": 9.2,
        }

        trade = simulate_trade(signal, exec_df, np.nan, 10_000, StrategyConfig())

        self.assertIsNotNone(trade)
        assert trade is not None
        self.assertEqual(trade.entry_price, 10.4)

    def test_entry_minute_touching_stop_is_conservative_loss(self) -> None:
        exec_df = candles([
            ("2025-01-01 00:20", 10.0, 10.3, 9.1, 10.1),
            ("2025-01-01 00:21", 10.1, 12.0, 10.0, 11.5),
        ])
        signal = {
            "time": pd.Timestamp("2025-01-01 00:20", tz="UTC"),
            "trigger_end_time": pd.Timestamp("2025-01-01 00:21", tz="UTC"),
            "direction": "long",
            "entry_price": 10.2,
            "sweep_extreme": 9.2,
        }

        trade = simulate_trade(signal, exec_df, np.nan, 10_000, StrategyConfig())

        self.assertIsNotNone(trade)
        assert trade is not None
        self.assertTrue(trade.ambiguous_entry_bar)
        self.assertEqual(trade.exit_price, 9.2)
        self.assertEqual(trade.r_multiple, -1.0)


if __name__ == "__main__":
    unittest.main()
