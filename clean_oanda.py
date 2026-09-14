"""
Cleans OANDA XAU/USD CSV data and writes one master CSV.

The input must contain exactly the source fields used by the pipeline:
timestamp, open, high, low, close, volume.

Timestamps are normalized to UTC and a JST timestamp is added for session
analysis. Suspicious rows are flagged, not repaired or interpolated.

Usage:
    python clean_oanda.py m5
    python clean_oanda.py m1
    python clean_oanda.py h1 --input raw_data/oanda/BTC_USD_H1_20160101_20260601.csv \
        --output data/processed/oanda_btcusd_h1_master.csv
"""

import argparse
import os

import pandas as pd


INPUT_FILES = {
    "m1": "raw_data/oanda/XAU_USD_M1_2015_2025.csv",
    "m5": "raw_data/oanda/XAU_USD_M5_2015_2025.csv",
}
OUTPUT_FILES = {
    "m1": "data/processed/oanda_xauusd_m1_master.csv",
    "m5": "data/processed/oanda_xauusd_m5_master.csv",
}
TIMEFRAME_SECONDS = {"m1": 60, "m5": 5 * 60, "m15": 15 * 60, "h1": 60 * 60}
WEEKEND_GAP_THRESHOLD_HOURS = 20
SHORT_GAP_THRESHOLD_MINUTES = 15
REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def load_data(input_file: str) -> pd.DataFrame:
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"Input file not found: {input_file}")

    df = pd.read_csv(input_file)
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")

    extra = [column for column in df.columns if column not in REQUIRED_COLUMNS]
    if extra:
        print(f"Ignoring extra input columns: {', '.join(extra)}")
    return df[REQUIRED_COLUMNS].copy()


def clean(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    parsed_timestamp = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df["is_bad_timestamp"] = parsed_timestamp.isna()
    df["datetime_utc"] = parsed_timestamp
    df["datetime_jst"] = parsed_timestamp.dt.tz_convert("Asia/Tokyo")

    before = len(df)
    df = (
        df.assign(_sort_timestamp=parsed_timestamp)
        .sort_values("_sort_timestamp", na_position="last")
        .drop(columns="_sort_timestamp")
        .reset_index(drop=True)
    )
    duplicate_timestamp = df["datetime_utc"].duplicated(keep="first") & df["datetime_utc"].notna()
    df = df.loc[~duplicate_timestamp].reset_index(drop=True)
    print(f"Deduplicated: {before} -> {len(df)} rows")

    numeric_columns = ["open", "high", "low", "close", "volume"]
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df["is_bad_numeric"] = df[numeric_columns].isna().any(axis=1)
    df["is_ohlc_invalid"] = (
        (df["high"] < df[["open", "close"]].max(axis=1))
        | (df["low"] > df[["open", "close"]].min(axis=1))
    )
    df["is_flat_candle"] = (
        (df["open"] == df["high"])
        & (df["high"] == df["low"])
        & (df["low"] == df["close"])
    )

    df["gap_seconds"] = df["datetime_utc"].diff().dt.total_seconds()
    df["is_weekend_gap"] = df["gap_seconds"] > WEEKEND_GAP_THRESHOLD_HOURS * 3600
    df["is_short_data_gap"] = (
        (df["gap_seconds"] > SHORT_GAP_THRESHOLD_MINUTES * 60)
        & ~df["is_weekend_gap"]
    )
    df["is_suspect"] = (
        df["is_bad_timestamp"]
        | df["is_bad_numeric"]
        | df["is_ohlc_invalid"]
        | df["is_flat_candle"]
    )

    print(f"Flagged {df['is_bad_timestamp'].sum()} bad timestamps.")
    print(f"Flagged {df['is_bad_numeric'].sum()} rows with bad OHLCV values.")
    print(f"Flagged {df['is_ohlc_invalid'].sum()} OHLC-invalid rows.")
    print(f"Flagged {df['is_flat_candle'].sum()} flat candles.")
    print(
        f"Flagged {df['is_weekend_gap'].sum()} weekend gaps and "
        f"{df['is_short_data_gap'].sum()} short/abnormal data gaps."
    )
    print(f"Expected candle interval: {TIMEFRAME_SECONDS[timeframe]} seconds.")
    return df


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean one OANDA candle CSV without fabricating missing prices.")
    parser.add_argument("timeframe", nargs="?", default="m5", choices=sorted(TIMEFRAME_SECONDS))
    parser.add_argument("--input", help="Raw OANDA CSV; required for non-XAU presets")
    parser.add_argument("--output", help="Cleaned master CSV path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    timeframe = args.timeframe
    input_file = args.input or INPUT_FILES.get(timeframe)
    output_file = args.output or OUTPUT_FILES.get(timeframe)
    if not input_file or not output_file:
        raise ValueError("Use --input and --output when cleaning M15/H1 or a non-XAU instrument.")

    print(f"Loading OANDA {timeframe} data...")
    df = clean(load_data(input_file), timeframe)

    columns = [
        "timestamp", "datetime_utc", "datetime_jst",
        "open", "high", "low", "close", "volume",
        "is_bad_timestamp", "is_bad_numeric", "is_ohlc_invalid",
        "is_flat_candle", "gap_seconds", "is_weekend_gap",
        "is_short_data_gap", "is_suspect",
    ]
    output_directory = os.path.dirname(output_file)
    if output_directory:
        os.makedirs(output_directory, exist_ok=True)
    df[columns].to_csv(output_file, index=False)
    print(f"Saved: {output_file} ({len(df)} rows)")
    print(f"Suspect rows: {df['is_suspect'].sum()} ({df['is_suspect'].mean() * 100:.2f}%)")


if __name__ == "__main__":
    main()
