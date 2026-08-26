"""
Cleans and merges the raw per-year Dukascopy CSVs (produced by fetch_xauusd.js)
into a single master CSV, WITHOUT fabricating any price data.

Design principle: never invent OHLC values. Where data looks suspicious
(flat candle, missing candle, weekend gap), we FLAG it with a boolean
column instead of overwriting/interpolating. The backtest/swing-detection
code decides what to do with flagged rows (e.g. skip them).

Usage:
    pip install pandas pytz --break-system-packages
    python clean_and_merge.py
"""

import pandas as pd
import glob
import os

RAW_DIR = "data/raw/m5"      
OUT_FILE = "data/processed/xauusd_m5_master.csv"   
TIMEFRAME_SECONDS = 5 * 60    # change to 60 when cleaning the 1-min set
WEEKEND_GAP_THRESHOLD_HOURS = 20  # anything bigger than this = weekend gap
SHORT_GAP_THRESHOLD_MINUTES = 15  # anything bigger than one missed candle = flagged gap


def load_raw_files():
    files = sorted(glob.glob(os.path.join(RAW_DIR, "*.csv")))
    if not files:
        raise FileNotFoundError(f"No CSV files found in {RAW_DIR}/. Run fetch_xauusd.js first.")
    dfs = []
    for f in files:
        df = pd.read_csv(f)
        dfs.append(df)
        print(f"  loaded {f}: {len(df)} rows")
    merged = pd.concat(dfs, ignore_index=True)
    return merged


def clean(df: pd.DataFrame) -> pd.DataFrame:
    # --- basic dedup + sort ---
    before = len(df)
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    print(f"Deduplicated: {before} -> {len(df)} rows")

    # --- timestamp handling: keep canonical UTC datetime ---
    df["datetime_utc"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)

    # convenience columns for session-based assets later (not needed for XAUUSD itself,
    # but keeping the pattern consistent across your pipeline)
    df["datetime_ny"] = df["datetime_utc"].dt.tz_convert("America/New_York")
    df["datetime_london"] = df["datetime_utc"].dt.tz_convert("Europe/London")

    # --- numeric sanity check ---
    numeric_cols = ["open", "high", "low", "close", "volume"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    n_bad_numeric = df[numeric_cols].isna().any(axis=1).sum()
    if n_bad_numeric:
        print(f"WARNING: {n_bad_numeric} rows have non-numeric OHLCV values. Flagging, not dropping.")
    df["is_bad_numeric"] = df[numeric_cols].isna().any(axis=1)

    # --- OHLC logical sanity check (high must be the max, low must be the min) ---
    df["is_ohlc_invalid"] = (
        (df["high"] < df[["open", "close"]].max(axis=1)) |
        (df["low"] > df[["open", "close"]].min(axis=1))
    )
    n_invalid = df["is_ohlc_invalid"].sum()
    if n_invalid:
        print(f"WARNING: {n_invalid} rows fail OHLC sanity (high/low don't bound open/close). Flagged.")

    # --- flat candle flag (O=H=L=C) — likely a single-tick / dead-liquidity candle ---
    df["is_flat_candle"] = (
        (df["open"] == df["high"]) &
        (df["high"] == df["low"]) &
        (df["low"] == df["close"])
    )
    n_flat = df["is_flat_candle"].sum()
    print(f"Flagged {n_flat} flat candles (O=H=L=C) — left as-is, NOT overwritten.")

    # --- gap detection between consecutive candles ---
    df["gap_seconds"] = df["datetime_utc"].diff().dt.total_seconds()
    expected = TIMEFRAME_SECONDS

    df["is_weekend_gap"] = df["gap_seconds"] > (WEEKEND_GAP_THRESHOLD_HOURS * 3600)
    df["is_short_data_gap"] = (
        (df["gap_seconds"] > (SHORT_GAP_THRESHOLD_MINUTES * 60)) &
        (~df["is_weekend_gap"])
    )

    n_weekend = df["is_weekend_gap"].sum()
    n_short_gap = df["is_short_data_gap"].sum()
    print(f"Flagged {n_weekend} weekend gaps and {n_short_gap} short/abnormal data gaps.")
    print("No rows were fabricated to fill these gaps — the timestamp sequence simply skips them.")

    # --- overall usability flag: convenience column for backtest code ---
    # A row is "safe to use" for swing/sweep/BOS logic if none of these fired.
    df["is_suspect"] = (
        df["is_bad_numeric"] |
        df["is_ohlc_invalid"] |
        df["is_flat_candle"]
    )

    return df


def main():
    print("Loading raw per-year files...")
    df = load_raw_files()

    print("\nCleaning and flagging...")
    df = clean(df)

    # reorder columns for readability
    cols = [
        "timestamp", "datetime_utc", "datetime_ny", "datetime_london",
        "open", "high", "low", "close", "volume",
        "is_bad_numeric", "is_ohlc_invalid", "is_flat_candle",
        "gap_seconds", "is_weekend_gap", "is_short_data_gap",
        "is_suspect",
    ]
    df = df[cols]

    df.to_csv(OUT_FILE, index=False)
    print(f"\nSaved master file: {OUT_FILE} ({len(df)} rows)")

    print("\n--- Summary ---")
    print(f"Total candles:        {len(df)}")
    print(f"Suspect candles:      {df['is_suspect'].sum()} ({df['is_suspect'].mean()*100:.2f}%)")
    print(f"Weekend gaps:         {df['is_weekend_gap'].sum()}")
    print(f"Short/abnormal gaps:  {df['is_short_data_gap'].sum()}")
    print(f"Date range:           {df['datetime_utc'].min()} to {df['datetime_utc'].max()}")


if __name__ == "__main__":
    main()