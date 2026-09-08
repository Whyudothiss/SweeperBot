"""
Converts a cleaned master CSV (from clean_and_merge.py) into Parquet.
 
Usage:
    pip install pyarrow --break-system-packages
    python csv_to_parquet.py xauusd_m5_master.csv
    python csv_to_parquet.py xauusd_m1_master.csv
    python csv_to_parquet.py data/processed/oanda_xauusd_m5_master.csv
"""
 
import sys
from pathlib import Path

import pandas as pd
 
def convert(csv_path: str):
    source = Path(csv_path)
    if source.suffix.lower() != ".csv":
        raise ValueError(f"Expected a CSV file, got: {source}")
    parquet_path = source.with_suffix(".parquet")
 
    print(f"Reading {source} ...")
    df = pd.read_csv(source)

    required_columns = {"datetime_utc", "open", "high", "low", "close"}
    missing_columns = sorted(required_columns - set(df.columns))
    if missing_columns:
        raise ValueError(
            f"{source} is not a cleaned master file; missing columns: "
            f"{', '.join(missing_columns)}"
        )
 
    # Parse with utc=True first: this normalizes every row to a single
    # consistent UTC-based dtype, avoiding the "mixed timezone offsets"
    # error that occurs when reading literal offset strings (which differ
    # across DST transitions, e.g. -05:00 vs -04:00 for America/New_York).
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"], utc=True)

    # Dukascopy masters contain New York/London columns; OANDA masters contain
    # a Japan-time column.  Preserve whichever optional display timezones the
    # cleaned source provides instead of assuming one vendor's schema.
    timezone_columns = {
        "datetime_ny": "America/New_York",
        "datetime_london": "Europe/London",
        "datetime_jst": "Asia/Tokyo",
    }
    for column, timezone in timezone_columns.items():
        if column in df.columns:
            df[column] = pd.to_datetime(df[column], utc=True).dt.tz_convert(timezone)
 
    print(f"Writing {parquet_path} ...")
    df.to_parquet(parquet_path, engine="pyarrow", compression="snappy", index=False)
 
    import os
    csv_size = os.path.getsize(source) / 1e6
    parquet_size = os.path.getsize(parquet_path) / 1e6
    print(f"CSV size:     {csv_size:.1f} MB")
    print(f"Parquet size: {parquet_size:.1f} MB  ({parquet_size/csv_size*100:.1f}% of original)")
 
if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python csv_to_parquet.py <path_to_master.csv>")
        sys.exit(1)
    convert(sys.argv[1])
