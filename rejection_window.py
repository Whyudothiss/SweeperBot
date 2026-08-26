"""
Sweeps max_rejection_wait_bars (0 = strict same-candle rejection only, up to
some larger window) with lookback/lookforward held fixed, to see whether
allowing multi-candle rejection helps or hurts XAUUSD 5-min performance.

Usage:
    python sweep_rejection_window.py
"""

import pandas as pd
from strategy_engine import StrategyConfig, run_backtest

SIGNAL_PATH = "data/processed/xauusd_m5_master.parquet"
EXEC_PATH = "data/processed/xauusd_m1_master.parquet"

# candidate values to test: 0 = strict same-candle only
CANDIDATE_VALUES = [0, 1, 2, 3, 5, 10, 20, 50]

# hold these fixed while sweeping the rejection window
FIXED_LOOKBACK = 2
FIXED_LOOKFORWARD = 1


def main():
    print("Loading data...")
    signal_df = pd.read_parquet(SIGNAL_PATH)
    exec_df = pd.read_parquet(EXEC_PATH)

    results = []

    for wait_bars in CANDIDATE_VALUES:
        cfg = StrategyConfig(
            lookback=FIXED_LOOKBACK,
            lookforward=FIXED_LOOKFORWARD,
            max_rejection_wait_bars=wait_bars,
            direction="both",
        )
        print(f"\nRunning with max_rejection_wait_bars={wait_bars} ...")
        trades, final_equity = run_backtest(signal_df, exec_df, cfg)

        n = len(trades)
        if n == 0:
            results.append({
                "max_rejection_wait_bars": wait_bars,
                "trades": 0, "win_rate_pct": None,
                "avg_r": None, "total_return_pct": None,
            })
            print("  No trades generated.")
            continue

        wins = sum(1 for t in trades if t.pnl_dollars > 0)
        avg_r = sum(t.r_multiple for t in trades) / n
        total_return_pct = (final_equity - cfg.base_capital) / cfg.base_capital * 100

        results.append({
            "max_rejection_wait_bars": wait_bars,
            "trades": n,
            "win_rate_pct": round(wins / n * 100, 1),
            "avg_r": round(avg_r, 2),
            "total_return_pct": round(total_return_pct, 2),
        })
        print(f"  Trades: {n}, Win rate: {wins/n*100:.1f}%, Avg R: {avg_r:.2f}, "
              f"Return: {total_return_pct:.2f}%")

    results_df = pd.DataFrame(results)
    results_df.to_csv("rejection_window_sweep_results.csv", index=False)

    print("\n=== Summary (sorted by total return) ===")
    print(results_df.sort_values("total_return_pct", ascending=False).to_string(index=False))
    print("\nSaved: rejection_window_sweep_results.csv")


if __name__ == "__main__":
    main()