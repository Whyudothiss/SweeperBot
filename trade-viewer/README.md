# Trade Replay Viewer

A local browser tool for reviewing the current XAUUSD strategy one trade at a
time. It reads the parquet candle data already in the parent project and
re-runs the configured strategy when the local API starts; trades are not
stored inside the Parquet files. It does not upload the price data anywhere.

From this folder, run:

```bash
npm run dev
```

Then open `http://localhost:3000`.

The viewer starts by replaying the current strict configuration: lookback 2,
lookforward 1, and rejection window 0. Use a trade number, a date, or the next
and previous buttons to inspect trades. The M5 chart marks the sweep wick,
BOS, and entry; the M1 chart marks entry, initial stop, and exit.
The M5 chart also marks the swing level broken by the BOS, so the setup can be
checked from the swept swing through to the confirmation candle.

## Replay OANDA data

After creating `oanda_xauusd_m5_master.parquet` and
`oanda_xauusd_m1_master.parquet` in `../data/processed`, start the viewer with
these environment variables:

```bash
SWEEPER_SIGNAL_PATH=data/processed/oanda_xauusd_m5_master.parquet \
SWEEPER_EXEC_PATH=data/processed/oanda_xauusd_m1_master.parquet \
npm run dev
```

The same strict strategy configuration is replayed, but all candles and trades
come from the OANDA files. Omit the variables to return to the original
Dukascopy data.
