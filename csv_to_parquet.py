"""
Converts a cleaned master CSV (from clean_and_merge.py) into Parquet.
 
Usage:
    pip install pyarrow --break-system-packages
    python csv_to_parquet.py xauusd_m5_master.csv
    python csv_to_parquet.py xauusd_m1_master.csv
"""
 
import sys
import pandas as pd
 
def convert(csv_path: str):
    parquet_path = csv_path.replace(".csv", ".parquet")
 
    print(f"Reading {csv_path} ...")
    df = pd.read_csv(csv_path)
 
    # Parse with utc=True first: this normalizes every row to a single
    # consistent UTC-based dtype, avoiding the "mixed timezone offsets"
    # error that occurs when reading literal offset strings (which differ
    # across DST transitions, e.g. -05:00 vs -04:00 for America/New_York).
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"], utc=True)
    df["datetime_ny"] = pd.to_datetime(df["datetime_ny"], utc=True).dt.tz_convert("America/New_York")
    df["datetime_london"] = pd.to_datetime(df["datetime_london"], utc=True).dt.tz_convert("Europe/London")
 
    print(f"Writing {parquet_path} ...")
    df.to_parquet(parquet_path, engine="pyarrow", compression="snappy", index=False)
 
    import os
    csv_size = os.path.getsize(csv_path) / 1e6
    parquet_size = os.path.getsize(parquet_path) / 1e6
    print(f"CSV size:     {csv_size:.1f} MB")
    print(f"Parquet size: {parquet_size:.1f} MB  ({parquet_size/csv_size*100:.1f}% of original)")
 
if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python csv_to_parquet.py <path_to_master.csv>")
        sys.exit(1)
    convert(sys.argv[1])