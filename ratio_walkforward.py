"""Walk-forward swing-ratio study for the XAUUSD M5/M1 strategy.

Ratios are selected only from the training period.  The selected candidates
are then run unchanged on the later validation period, so validation results
cannot influence the selection.

Default periods:
  training:   2015-01-01 through 2022-12-31
  validation: 2023-01-01 through 2025-12-31

Example:
  python3 ratio_walkforward.py
"""

from __future__ import annotations

import argparse
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd

from strategy_engine import StrategyConfig, Trade, run_backtest


DEFAULT_SIGNAL_PATH = "data/processed/oanda_xauusd_m5_master.parquet"
DEFAULT_EXEC_PATH = "data/processed/oanda_xauusd_m1_master.parquet"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select swing ratios on training data, then validate unchanged candidates.")
    parser.add_argument("--signal-path", default=DEFAULT_SIGNAL_PATH, help="M5 signal Parquet path")
    parser.add_argument("--exec-path", default=DEFAULT_EXEC_PATH, help="M1 execution Parquet path")
    parser.add_argument("--train-start", default="2015-01-01")
    parser.add_argument("--train-end", default="2023-01-01", help="Exclusive UTC end date")
    parser.add_argument("--validation-start", default="2023-01-01")
    parser.add_argument("--validation-end", default="2026-01-01", help="Exclusive UTC end date")
    parser.add_argument("--max-lookback", type=int, default=20)
    parser.add_argument("--max-lookforward", type=int, default=10)
    parser.add_argument("--min-trades", type=int, default=100, help="Minimum completed training trades eligible for selection")
    parser.add_argument("--top-n", type=int, default=20, help="Training-selected ratios to validate and run over full history")
    parser.add_argument("--output-dir", default="results/ratio_walkforward")
    parser.add_argument("--all-full", action="store_true", help="Also run every grid ratio over the complete 2015-2025 period")
    return parser.parse_args()


def utc(value: str) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC")


def slice_period(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    time = df["datetime_utc"]
    return df.loc[(time >= start) & (time < end)].reset_index(drop=True)


def completed_trades(trades: list[Trade]) -> list[Trade]:
    """Exclude only the forced close produced by a truncated period boundary."""
    return [trade for trade in trades if trade.exit_reason != "data_end"]


def metrics(trades: list[Trade], cfg: StrategyConfig) -> dict[str, float | int | None]:
    completed = completed_trades(trades)
    dropped_at_boundary = len(trades) - len(completed)
    if not completed:
        return {
            "trades": 0, "dropped_data_end_trades": dropped_at_boundary,
            "win_rate_pct": None, "avg_r": None, "median_r": None,
            "profit_factor": None, "total_return_pct": None,
            "max_drawdown_pct": None, "return_over_drawdown": None,
            "ambiguous_intrabar_events": 0, "ambiguous_pct": None,
            "ambiguous_entry_bars": 0,
        }

    pnl = np.array([trade.pnl_dollars for trade in completed], dtype=float)
    r_values = np.array([trade.r_multiple for trade in completed], dtype=float)
    wins = pnl[pnl > 0].sum()
    losses = -pnl[pnl < 0].sum()
    # Every completed trade uses the current equity from run_backtest.  A
    # discarded data_end trade is necessarily the final trade, so summing
    # retained P&L gives the same equity as a clean period-end simulation.
    final_equity = cfg.base_capital + pnl.sum()
    equity_curve = np.concatenate(([cfg.base_capital], cfg.base_capital + np.cumsum(pnl)))
    running_peak = np.maximum.accumulate(equity_curve)
    drawdowns = (running_peak - equity_curve) / running_peak
    max_drawdown_pct = float(drawdowns.max() * 100)
    total_return_pct = float((final_equity / cfg.base_capital - 1) * 100)
    ambiguous = sum(trade.ambiguous_intrabar_events for trade in completed)
    ambiguous_entries = sum(trade.ambiguous_entry_bar for trade in completed)
    count = len(completed)
    return {
        "trades": count,
        "dropped_data_end_trades": dropped_at_boundary,
        "win_rate_pct": round(float((pnl > 0).mean() * 100), 2),
        "avg_r": round(float(r_values.mean()), 4),
        "median_r": round(float(np.median(r_values)), 4),
        "profit_factor": round(float(wins / losses), 4) if losses > 0 else None,
        "total_return_pct": round(total_return_pct, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "return_over_drawdown": round(total_return_pct / max_drawdown_pct, 2) if max_drawdown_pct > 0 else None,
        "ambiguous_intrabar_events": ambiguous,
        "ambiguous_pct": round(float(ambiguous / count * 100), 2),
        "ambiguous_entry_bars": ambiguous_entries,
    }


def run_ratio(
    signal_df: pd.DataFrame,
    exec_df: pd.DataFrame,
    lookback: int,
    lookforward: int,
    entry_start: pd.Timestamp | None = None,
) -> dict[str, float | int | None]:
    cfg = StrategyConfig(
        lookback=lookback,
        lookforward=lookforward,
        max_rejection_wait_bars=0,
        direction="both",
    )
    trades, _ = run_backtest(signal_df, exec_df, cfg, entry_start_time=entry_start)
    return metrics(trades, cfg)


def combos(max_lookback: int, max_lookforward: int) -> list[tuple[int, int]]:
    if max_lookback < 1 or max_lookforward < 1:
        raise ValueError("Maximum lookback/lookforward values must be positive.")
    return [
        (lookback, lookforward)
        for lookback in range(1, max_lookback + 1)
        for lookforward in range(1, min(lookback, max_lookforward) + 1)
    ]


def write_csv(rows: list[dict], path: Path) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


def main() -> None:
    args = parse_args()
    train_start, train_end = utc(args.train_start), utc(args.train_end)
    validation_start, validation_end = utc(args.validation_start), utc(args.validation_end)
    if not train_start < train_end <= validation_start < validation_end:
        raise ValueError("Periods must be chronological and non-overlapping: train_start < train_end <= validation_start < validation_end.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("Loading OANDA Parquet data...")
    signal_all = pd.read_parquet(args.signal_path)
    exec_all = pd.read_parquet(args.exec_path)
    train_signal = slice_period(signal_all, train_start, train_end)
    train_exec = slice_period(exec_all, train_start, train_end)
    validation_signal = slice_period(signal_all, validation_start, validation_end)
    validation_exec = slice_period(exec_all, validation_start, validation_end)
    full_signal = slice_period(signal_all, train_start, validation_end)
    full_exec = slice_period(exec_all, train_start, validation_end)

    print(f"Training:   {train_start.date()} to {(train_end - pd.Timedelta(nanoseconds=1)).date()} ({len(train_signal):,} M5 / {len(train_exec):,} M1 bars)")
    print(f"Validation: {validation_start.date()} to {(validation_end - pd.Timedelta(nanoseconds=1)).date()} ({len(validation_signal):,} M5 / {len(validation_exec):,} M1 bars)")

    grid = combos(args.max_lookback, args.max_lookforward)
    print(f"\nTraining grid: {len(grid)} ratios, rejection window fixed at 0...")
    train_rows: list[dict] = []
    started = perf_counter()
    train_path = output_dir / "training_grid.csv"
    for number, (lookback, lookforward) in enumerate(grid, start=1):
        row = {"lookback": lookback, "lookforward": lookforward}
        row.update(run_ratio(train_signal, train_exec, lookback, lookforward))
        train_rows.append(row)
        write_csv(train_rows, train_path)
        elapsed = perf_counter() - started
        print(f"[{number:>3}/{len(grid)}] ({lookback:>2},{lookforward:>2})  trades={row['trades']:>5}  avg_R={row['avg_r']}  return={row['total_return_pct']}%  elapsed={elapsed:.0f}s")

    eligible = [row for row in train_rows if row["trades"] >= args.min_trades and row["total_return_pct"] is not None]
    eligible.sort(key=lambda row: row["total_return_pct"], reverse=True)
    candidates = eligible[:args.top_n]
    if not candidates:
        raise RuntimeError(f"No ratios had at least {args.min_trades} completed training trades.")

    print(f"\nValidating the top {len(candidates)} training ratios unchanged...")
    combined_rows: list[dict] = []
    for rank, train_row in enumerate(candidates, start=1):
        lookback, lookforward = train_row["lookback"], train_row["lookforward"]
        # Supply prior history so a 2023 setup can reference structure known
        # in late 2022, while scoring only trades entered from 2023 onward.
        validation = run_ratio(full_signal, full_exec, lookback, lookforward, entry_start=validation_start)
        full = run_ratio(full_signal, full_exec, lookback, lookforward)
        row = {"training_rank": rank, "lookback": lookback, "lookforward": lookforward}
        row.update({f"train_{key}": value for key, value in train_row.items() if key not in {"lookback", "lookforward"}})
        row.update({f"validation_{key}": value for key, value in validation.items()})
        row.update({f"full_{key}": value for key, value in full.items()})
        combined_rows.append(row)
        print(f"[{rank:>2}/{len(candidates)}] ({lookback:>2},{lookforward:>2})  validation avg_R={validation['avg_r']}  return={validation['total_return_pct']}%")

    if args.all_full:
        selected = {(row["lookback"], row["lookforward"]): row for row in combined_rows}
        print("\nRunning the remaining ratios on full history (descriptive only)...")
        for lookback, lookforward in grid:
            if (lookback, lookforward) in selected:
                continue
            full = run_ratio(full_signal, full_exec, lookback, lookforward)
            selected[(lookback, lookforward)] = {
                "training_rank": None, "lookback": lookback, "lookforward": lookforward,
                **{f"full_{key}": value for key, value in full.items()},
            }
        full_grid_rows = list(selected.values())
        write_csv(full_grid_rows, output_dir / "full_history_grid.csv")

    combined_rows.sort(key=lambda row: row["training_rank"])
    combined_path = output_dir / "selected_ratio_validation.csv"
    write_csv(combined_rows, combined_path)

    pd.DataFrame(combined_rows).to_json(output_dir / "selected_ratio_validation.json", orient="records", indent=2)
    print(f"\nSaved training grid: {train_path}")
    print(f"Saved selected-ratio validation: {combined_path}")
    print("Choose a ratio from validation performance and neighbouring-ratio consistency, not the full-history rank.")


if __name__ == "__main__":
    main()
