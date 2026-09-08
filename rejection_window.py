"""
Sweeps max_rejection_wait_bars (0 = strict same-candle rejection only, up to
some larger window) with lookback/lookforward held fixed, to see whether
allowing multi-candle rejection helps or hurts XAUUSD 5-min performance.

Usage:
    python rejection_window.py
    python rejection_window.py \
        --signal-path data/processed/oanda_xauusd_m5_master.parquet \
        --exec-path data/processed/oanda_xauusd_m1_master.parquet \
        --output oanda_rejection_window_sweep_results.csv
"""

import argparse
import pandas as pd
from strategy_engine import StrategyConfig, run_backtest

SIGNAL_PATH = "data/processed/xauusd_m5_master.parquet"
EXEC_PATH = "data/processed/xauusd_m1_master.parquet"

# candidate values to test: 0 = strict same-candle only
CANDIDATE_VALUES = [0]

# hold these fixed while sweeping the rejection window
FIXED_LOOKBACK = 2
FIXED_LOOKFORWARD = 1


def parse_args():
    parser = argparse.ArgumentParser(
        description="Sweep rejection windows using M5 signal data and M1 execution data."
    )
    parser.add_argument("--signal-path", default=SIGNAL_PATH, help="M5 signal Parquet path")
    parser.add_argument("--exec-path", default=EXEC_PATH, help="M1 execution Parquet path")
    parser.add_argument(
        "--output", default="rejection_window_sweep_results.csv", help="CSV path for sweep results"
    )
    parser.add_argument(
        "--wait-bars", nargs="+", type=int, default=CANDIDATE_VALUES,
        help="Rejection-window values to test (default: 0)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    print("Loading data...")
    signal_df = pd.read_parquet(args.signal_path)
    exec_df = pd.read_parquet(args.exec_path)
    print(f"  Signal data: {args.signal_path} ({len(signal_df):,} M5 bars)")
    print(f"  Execution data: {args.exec_path} ({len(exec_df):,} M1 bars)")

    results = []

    for wait_bars in args.wait_bars:
        if wait_bars < 0:
            raise ValueError("--wait-bars values must be zero or positive")
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
                "ambiguous_intrabar_events": 0,
            })
            print("  No trades generated.")
            continue

        wins = sum(1 for t in trades if t.pnl_dollars > 0)
        avg_r = sum(t.r_multiple for t in trades) / n
        ambiguous_events = sum(t.ambiguous_intrabar_events for t in trades)
        total_return_pct = (final_equity - cfg.base_capital) / cfg.base_capital * 100

        results.append({
            "max_rejection_wait_bars": wait_bars,
            "trades": n,
            "win_rate_pct": round(wins / n * 100, 1),
            "avg_r": round(avg_r, 2),
            "total_return_pct": round(total_return_pct, 2),
            "ambiguous_intrabar_events": ambiguous_events,
        })
        print(f"  Trades: {n}, Win rate: {wins/n*100:.1f}%, Avg R: {avg_r:.2f}, "
              f"Return: {total_return_pct:.2f}%, "
              f"Ambiguous 1-min events: {ambiguous_events}")

    results_df = pd.DataFrame(results)
    results_df.to_csv(args.output, index=False)

    print("\n=== Summary (sorted by total return) ===")
    print(results_df.sort_values("total_return_pct", ascending=False).to_string(index=False))
    print(f"\nSaved: {args.output}")


if __name__ == "__main__":
    main()
