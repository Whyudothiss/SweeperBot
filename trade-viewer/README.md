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

The viewer defaults to OANDA XAUUSD data with lookback 18, lookforward 6,
rejection window 0, 0.5% risk, and a stop exactly at the sweep wick. Use a
trade number, a date, or the next and previous buttons to inspect trades. The
M5 chart uses compact arrows for the swept swing, sweep wick, BOS-source swing,
and first-touch BOS entry. The M1 chart marks entry, initial stop, and exit.

## Override the replay configuration

After creating `oanda_xauusd_m5_master.parquet` and
`oanda_xauusd_m1_master.parquet` in `../data/processed`, a normal `npm run dev`
uses them automatically. For example, replay the walk-forward return winner
instead of the default video-parity ratio with:

```bash
SWEEPER_LOOKBACK=13 SWEEPER_LOOKFORWARD=3 \
npm run dev
```

Available overrides are `SWEEPER_SIGNAL_PATH`, `SWEEPER_EXEC_PATH`,
`SWEEPER_LOOKBACK`, `SWEEPER_LOOKFORWARD`, `SWEEPER_RISK_PCT`, and
`SWEEPER_STOP_BUFFER_ATR_MULT`. Data paths are resolved from the parent project
directory.
