/**
 * Fetches XAUUSD 5-minute candles from Dukascopy, year by year,
 * and saves one raw CSV per year into ./raw_data/
 *
 * Usage:
 *   npm install dukascopy-node
 *   node fetch_xauusd.js
 *
 * Adjust START_YEAR / END_YEAR below as needed.
 */

const { getHistoricalRates } = require("dukascopy-node");
const fs = require("fs");
const path = require("path");

const START_YEAR = 2015;
const END_YEAR = 2025; // inclusive
const INSTRUMENT = "xauusd";
const TIMEFRAME = "m5";
const OUT_DIR = path.join(__dirname, "raw_data", TIMEFRAME);

if (!fs.existsSync(OUT_DIR)) {
  fs.mkdirSync(OUT_DIR, { recursive: true });
}

function candlesToCsv(candles) {
  const header = "timestamp,open,high,low,close,volume\n";
  const rows = candles
    .map(c => `${c.timestamp},${c.open},${c.high},${c.low},${c.close},${c.volume}`)
    .join("\n");
  return header + rows + "\n";
}

async function fetchYear(year) {
  const from = new Date(`${year}-01-01T00:00:00Z`);
  const to = new Date(`${year + 1}-01-01T00:00:00Z`);

  console.log(`Fetching ${INSTRUMENT} ${TIMEFRAME} for ${year} ...`);

  try {
    const candles = await getHistoricalRates({
      instrument: INSTRUMENT,
      dates: { from, to },
      timeframe: TIMEFRAME,
      format: "json",
      useCache: true, // caches raw .bi5 files locally so re-runs are faster
    });

    if (!candles || candles.length === 0) {
      console.warn(`  WARNING: 0 candles returned for ${year}. Market may be closed for full range, or fetch failed silently.`);
      return;
    }

    const csv = candlesToCsv(candles);
    const outPath = path.join(OUT_DIR, `${INSTRUMENT}_${TIMEFRAME}_${year}.csv`);
    fs.writeFileSync(outPath, csv);
    console.log(`  Saved ${candles.length} candles -> ${outPath}`);
  } catch (err) {
    console.error(`  ERROR fetching ${year}:`, err.message);
  }
}

(async () => {
  for (let year = START_YEAR; year <= END_YEAR; year++) {
    await fetchYear(year);
  }
  console.log("Done. Raw per-year CSVs are in ./raw_data/");
  console.log("Next: run clean_and_merge.py to produce one clean master CSV.");
})();
