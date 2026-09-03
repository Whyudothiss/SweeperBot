# Trade Replay Viewer

A local browser tool for reviewing the current XAUUSD strategy one trade at a
time. It reads the parquet data already in the parent project; it does not
upload the price data anywhere.

From this folder, run:

```bash
npm run dev
```

Then open `http://localhost:3000`.

The viewer starts by replaying the current strict configuration: lookback 2,
lookforward 1, and rejection window 0. Use a trade number, a date, or the next
and previous buttons to inspect trades. The M5 chart marks the sweep wick,
BOS, and entry; the M1 chart marks entry, initial stop, and exit.
