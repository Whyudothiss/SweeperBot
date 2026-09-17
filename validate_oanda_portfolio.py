"""Validate prepared OANDA Parquet files without modifying them."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


REQUIRED_COLUMNS = [
    "datetime_utc",
    "open",
    "high",
    "low",
    "close",
    "is_bad_timestamp",
    "is_bad_numeric",
    "is_ohlc_invalid",
    "is_flat_candle",
    "is_weekend_gap",
    "is_short_data_gap",
    "is_suspect",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data/processed")
    parser.add_argument("--include-xau", action="store_true")
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2026-01-01")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)
    paths = sorted(data_dir.glob("oanda_*_master.parquet"))
    if not args.include_xau:
        paths = [path for path in paths if "_xauusd_" not in path.name]
    if not paths:
        raise FileNotFoundError(f"No OANDA Parquets found under {data_dir}")

    start = pd.to_datetime(args.start, utc=True)
    end = pd.to_datetime(args.end, utc=True)
    failures: list[str] = []

    print("file,rows,first_utc,last_utc,flat,suspect,weekend_gaps,other_gaps")
    for path in paths:
        frame = pd.read_parquet(path, columns=REQUIRED_COLUMNS)
        timestamps = pd.to_datetime(frame["datetime_utc"], utc=True, errors="coerce")
        fatal_flags = frame[["is_bad_timestamp", "is_bad_numeric", "is_ohlc_invalid"]].any(axis=1)
        duplicate_count = int(timestamps.duplicated().sum())

        if frame.empty:
            failures.append(f"{path.name}: empty")
        if timestamps.isna().any():
            failures.append(f"{path.name}: null/invalid UTC timestamps")
        if not timestamps.is_monotonic_increasing:
            failures.append(f"{path.name}: timestamps are not sorted")
        if duplicate_count:
            failures.append(f"{path.name}: {duplicate_count} duplicate timestamps")
        if fatal_flags.any():
            failures.append(f"{path.name}: {int(fatal_flags.sum())} fatal-quality rows")
        if not frame.empty and (timestamps.iloc[0] < start or timestamps.iloc[-1] >= end):
            failures.append(f"{path.name}: timestamps fall outside [{start}, {end})")

        print(
            f"{path.name},{len(frame)},{timestamps.min()},{timestamps.max()},"
            f"{int(frame['is_flat_candle'].sum())},{int(frame['is_suspect'].sum())},"
            f"{int(frame['is_weekend_gap'].sum())},{int(frame['is_short_data_gap'].sum())}"
        )

    if failures:
        raise SystemExit("Validation failed:\n- " + "\n- ".join(failures))
    print(f"Validated {len(paths)} Parquet files successfully.")


if __name__ == "__main__":
    main()
