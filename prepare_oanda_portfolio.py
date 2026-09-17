"""Clean downloaded OANDA portfolio CSVs and write Parquet masters.

Run after ``fetch_oanda_portfolio.js`` with matching start/end dates.

Examples:
    python3 prepare_oanda_portfolio.py --preset portfolio
    python3 prepare_oanda_portfolio.py --preset remaining
    python3 prepare_oanda_portfolio.py --preset btcusd
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from clean_oanda import clean, load_data


PRESETS = {
    "xauusd": ("XAU_USD", ("M5", "M1")),
    "btcusd": ("BTC_USD", ("H1", "M1")),
    "spx": ("SPX500_USD", ("M15", "M1")),
    "ndx": ("NAS100_USD", ("M15", "M1")),
    "gbpusd": ("GBP_USD", ("M15", "M1")),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare OANDA portfolio Parquet datasets.")
    parser.add_argument("--preset", default="portfolio", choices=[*PRESETS, "remaining", "portfolio"])
    parser.add_argument("--start", default="2015-01-01", help="Inclusive UTC boundary")
    parser.add_argument("--end", default="2026-01-01", help="Exclusive UTC boundary")
    parser.add_argument("--input-dir", default="raw_data/oanda")
    parser.add_argument("--output-dir", default="data/processed")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.preset == "portfolio":
        names = PRESETS
    elif args.preset == "remaining":
        names = {name: config for name, config in PRESETS.items() if name != "xauusd"}
    else:
        names = {args.preset: PRESETS[args.preset]}
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    start_utc = pd.to_datetime(args.start, utc=True)
    end_utc = pd.to_datetime(args.end, utc=True)
    if end_utc <= start_utc:
        raise ValueError("--end must be later than --start")

    missing = []
    tasks = []
    for slug, (instrument, granularities) in names.items():
        for granularity in granularities:
            sources = sorted(input_dir.glob(f"{instrument}_{granularity}_*.csv"))
            destination = output_dir / f"oanda_{slug}_{granularity.lower()}_master.parquet"
            if sources:
                tasks.append((sources, destination, granularity.lower()))
            else:
                missing.append(input_dir / f"{instrument}_{granularity}_*.csv")

    if missing:
        paths = "\n".join(f"  {path}" for path in missing)
        raise FileNotFoundError(f"Missing downloaded files:\n{paths}\nRun the matching fetch command first.")

    for sources, destination, timeframe in tasks:
        print(f"\nPreparing {destination} from {len(sources)} raw chunk(s)...")
        chunks = []
        for source in sources:
            chunk = load_data(str(source))
            timestamps = pd.to_datetime(chunk["timestamp"], utc=True, errors="coerce")
            selected = chunk.loc[timestamps.between(start_utc, end_utc, inclusive="left")]
            if not selected.empty:
                chunks.append(selected)
            print(f"  selected {len(selected):,} rows from {source.name}")
        if not chunks:
            raise ValueError(f"No candles found in the requested period for {destination}")
        frame = clean(pd.concat(chunks, ignore_index=True), timeframe)
        frame.to_parquet(destination, engine="pyarrow", compression="snappy", index=False)
        print(
            f"Saved: {destination} ({len(frame):,} rows, "
            f"{frame['datetime_utc'].min()} to {frame['datetime_utc'].max()})"
        )


if __name__ == "__main__":
    main()
