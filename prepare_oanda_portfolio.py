"""Clean downloaded OANDA portfolio CSVs and write Parquet masters.

Run after ``fetch_oanda_portfolio.js`` with matching start/end dates.

Examples:
    python3 prepare_oanda_portfolio.py --preset portfolio
    python3 prepare_oanda_portfolio.py --preset btcusd
"""

from __future__ import annotations

import argparse
from pathlib import Path

from clean_oanda import clean, load_data


PRESETS = {
    "xauusd": ("XAU_USD", ("M5", "M1")),
    "btcusd": ("BTC_USD", ("H1", "M1")),
    "spx": ("SPX500_USD", ("M15", "M1")),
    "ndx": ("NAS100_USD", ("M15", "M1")),
    "gbpusd": ("GBP_USD", ("M15", "M1")),
}


def compact_date(value: str) -> str:
    return value[:10].replace("-", "")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare OANDA portfolio Parquet datasets.")
    parser.add_argument("--preset", default="portfolio", choices=[*PRESETS, "portfolio"])
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", default="2026-06-01")
    parser.add_argument("--input-dir", default="raw_data/oanda")
    parser.add_argument("--output-dir", default="data/processed")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    names = PRESETS if args.preset == "portfolio" else {args.preset: PRESETS[args.preset]}
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    start, end = compact_date(args.start), compact_date(args.end)

    missing = []
    tasks = []
    for slug, (instrument, granularities) in names.items():
        for granularity in granularities:
            source = input_dir / f"{instrument}_{granularity}_{start}_{end}.csv"
            destination = output_dir / f"oanda_{slug}_{granularity.lower()}_master.parquet"
            if source.exists():
                tasks.append((source, destination, granularity.lower()))
            else:
                missing.append(source)

    if missing:
        paths = "\n".join(f"  {path}" for path in missing)
        raise FileNotFoundError(f"Missing downloaded files:\n{paths}\nRun the matching fetch command first.")

    for source, destination, timeframe in tasks:
        print(f"\nPreparing {source}...")
        frame = clean(load_data(str(source)), timeframe)
        frame.to_parquet(destination, engine="pyarrow", compression="snappy", index=False)
        print(f"Saved: {destination} ({len(frame):,} rows)")


if __name__ == "__main__":
    main()
