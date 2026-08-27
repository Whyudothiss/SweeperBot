"""
Sweeps lookback (i) x lookforward (j) combinations to find which
swing-detection sensitivity produces the best XAUUSD 5-min performance.

lookback  (i): 1..MAX_LOOKBACK
lookforward (j): 1..MAX_LOOKFORWARD

By default only combos where lookback >= lookforward are tested, since a
swing point is usually expected to need more confirmation on the left
(established structure) than the right (how soon you can react). Set
REQUIRE_LOOKBACK_GE_LOOKFORWARD = False to test the full grid instead.

max_rejection_wait_bars is held at the StrategyConfig default here since
this sweep targets swing sensitivity, not rejection timing -- change
FIXED_MAX_REJECTION_WAIT_BARS below if you want it pinned elsewhere.

Usage:
    python sweep_lookback_lookforward.py
"""

import time
import pandas as pd
from strategy_engine import StrategyConfig, run_backtest

SIGNAL_PATH = "data/processed/xauusd_m5_master.parquet"
EXEC_PATH = "data/processed/xauusd_m1_master.parquet"

MAX_LOOKBACK = 20
MAX_LOOKFORWARD = 10
REQUIRE_LOOKBACK_GE_LOOKFORWARD = True

FIXED_MAX_REJECTION_WAIT_BARS = StrategyConfig().max_rejection_wait_bars  # 20, from default config

OUTPUT_CSV = "lookback_lookforward_sweep_results.csv"


def build_combos():
    combos = []
    for i in range(1, MAX_LOOKBACK + 1):
        for j in range(1, MAX_LOOKFORWARD + 1):
            if REQUIRE_LOOKBACK_GE_LOOKFORWARD and j > i:
                continue
            combos.append((i, j))
    return combos


def main():
    print("Loading data...")
    signal_df = pd.read_parquet(SIGNAL_PATH)
    exec_df = pd.read_parquet(EXEC_PATH)

    combos = build_combos()
    print(f"Testing {len(combos)} (lookback, lookforward) combinations...")

    results = []
    t_start = time.time()

    for n_done, (lookback, lookforward) in enumerate(combos, start=1):
        cfg = StrategyConfig(
            lookback=lookback,
            lookforward=lookforward,
            max_rejection_wait_bars=FIXED_MAX_REJECTION_WAIT_BARS,
            direction="both",
        )

        trades, final_equity = run_backtest(signal_df, exec_df, cfg)
        n = len(trades)

        if n == 0:
            row = {
                "lookback": lookback,
                "lookforward": lookforward,
                "trades": 0,
                "win_rate_pct": None,
                "avg_r": None,
                "total_return_pct": None,
                "ambiguous_intrabar_events": 0,
                "ambiguous_pct": None,
            }
        else:
            wins = sum(1 for t in trades if t.pnl_dollars > 0)
            avg_r = sum(t.r_multiple for t in trades) / n
            ambiguous_events = sum(t.ambiguous_intrabar_events for t in trades)
            total_return_pct = (final_equity - cfg.base_capital) / cfg.base_capital * 100
            row = {
                "lookback": lookback,
                "lookforward": lookforward,
                "trades": n,
                "win_rate_pct": round(wins / n * 100, 1),
                "avg_r": round(avg_r, 2),
                "total_return_pct": round(total_return_pct, 2),
                "ambiguous_intrabar_events": ambiguous_events,
                "ambiguous_pct": round(ambiguous_events / n * 100, 1),
            }

        results.append(row)

        elapsed = time.time() - t_start
        print(f"[{n_done}/{len(combos)}] lookback={lookback:>2} lookforward={lookforward:>2} "
              f"-> trades={row['trades']:>4} "
              f"win={row['win_rate_pct']} avg_r={row['avg_r']} "
              f"return={row['total_return_pct']}% "
              f"ambiguous={row['ambiguous_pct']}%  "
              f"(elapsed {elapsed:.0f}s)")

        # Write incrementally so a long sweep isn't lost if it's interrupted.
        pd.DataFrame(results).to_csv(OUTPUT_CSV, index=False)

    results_df = pd.DataFrame(results)

    print("\n=== Top 15 by total return (min 10 trades) ===")
    filtered = results_df[results_df["trades"] >= 10]
    print(filtered.sort_values("total_return_pct", ascending=False).head(15).to_string(index=False))

    print(f"\nSaved full grid: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()