/**
 * Fetches XAU/USD M1 candles from OANDA and saves one combined CSV.
 *
 * Requirements:
 *   Node.js 18+
 *
 * macOS/Linux:
 *   export OANDA_API_TOKEN="your_token_here"
 *   export OANDA_ENV="practice"   # or "live"
 *
 * Run:
 *   node fetch_oanda_xauusd.js
 */

const fs = require("fs");
const path = require("path");

const OANDA_API_TOKEN = process.env.OANDA_API_TOKEN;
const OANDA_ENV = process.env.OANDA_ENV || "practice";

if (!OANDA_API_TOKEN) {
  throw new Error(
    "Missing OANDA_API_TOKEN environment variable."
  );
}

const START_YEAR = 2015;
const END_YEAR = 2025;

const INSTRUMENT = "XAU_USD";
const GRANULARITY = "M5";
const PRICE_COMPONENT = "M";

const START_DATE = `${START_YEAR}-01-01T00:00:00Z`;
const END_DATE = `${END_YEAR + 1}-01-01T00:00:00Z`;

const API_HOST =
  OANDA_ENV === "live"
    ? "https://api-fxtrade.oanda.com"
    : "https://api-fxpractice.oanda.com";

const OUT_DIR = path.join(__dirname, "raw_data", "oanda");
const OUTPUT_FILE = path.join(
  OUT_DIR,
  `${INSTRUMENT}_${GRANULARITY}_${START_YEAR}_${END_YEAR}.csv`
);

const TEMP_FILE = `${OUTPUT_FILE}.part`;

function normaliseTimestampForDateParsing(timestamp) {
  // OANDA can return nanosecond timestamps.
  // JavaScript Date parsing reliably handles milliseconds.
  return timestamp.replace(
    /\.(\d{3})\d+Z$/,
    ".$1Z"
  );
}

function timestampToMs(timestamp) {
  return Date.parse(
    normaliseTimestampForDateParsing(timestamp)
  );
}

function escapeCsv(value) {
  const stringValue = String(value ?? "");

  if (
    stringValue.includes(",") ||
    stringValue.includes('"') ||
    stringValue.includes("\n")
  ) {
    return `"${stringValue.replace(/"/g, '""')}"`;
  }

  return stringValue;
}

function candleToCsvRow(candle) {
  const mid = candle.mid;

  return [
    candle.time,
    mid.o,
    mid.h,
    mid.l,
    mid.c,
    candle.volume
  ]
    .map(escapeCsv)
    .join(",");
}

async function fetchCandlesPage(fromTime) {
  const url = new URL(
    `${API_HOST}/v3/instruments/${INSTRUMENT}/candles`
  );

  url.searchParams.set("granularity", GRANULARITY);
  url.searchParams.set("price", PRICE_COMPONENT);
  url.searchParams.set("count", "5000");
  url.searchParams.set("from", fromTime);
  url.searchParams.set(
    "includeFirst",
    fromTime === START_DATE ? "true" : "false"
  );

  const response = await fetch(url, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${OANDA_API_TOKEN}`,
      Accept: "application/json"
    }
  });

  const responseText = await response.text();

  if (!response.ok) {
    throw new Error(
      `OANDA request failed with HTTP ${response.status}: ${responseText}`
    );
  }

  return JSON.parse(responseText);
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });

  // Remove only this script's previous partial download.
  if (fs.existsSync(TEMP_FILE)) {
    fs.unlinkSync(TEMP_FILE);
  }

  fs.writeFileSync(
    TEMP_FILE,
    "timestamp,open,high,low,close,volume\n"
  );

  const endMs = Date.parse(END_DATE);

  let fromTime = START_DATE;
  let totalWritten = 0;
  let pageNumber = 0;

  while (true) {
    pageNumber++;

    console.log(
      `Requesting page ${pageNumber}, starting at ${fromTime}...`
    );

    const result = await fetchCandlesPage(fromTime);
    const candles = result.candles || [];

    if (candles.length === 0) {
      console.log("No more candles returned by OANDA.");
      break;
    }

    let rows = [];
    let lastReturnedTime = null;
    let reachedEndDate = false;

    for (const candle of candles) {
      lastReturnedTime = candle.time;

      const candleMs = timestampToMs(candle.time);

      if (!Number.isFinite(candleMs)) {
        console.warn(
          `Skipping candle with invalid timestamp: ${candle.time}`
        );
        continue;
      }

      if (candleMs >= endMs) {
        reachedEndDate = true;
        break;
      }

      if (!candle.complete) {
        continue;
      }

      if (!candle.mid) {
        console.warn(
          `Skipping candle without midpoint data: ${candle.time}`
        );
        continue;
      }

      rows.push(candleToCsvRow(candle));
    }

    if (rows.length > 0) {
      fs.appendFileSync(TEMP_FILE, `${rows.join("\n")}\n`);
      totalWritten += rows.length;
    }

    console.log(
      `Received ${candles.length} candles; wrote ${rows.length}. Total: ${totalWritten}`
    );

    if (reachedEndDate) {
      break;
    }

    if (!lastReturnedTime) {
      throw new Error("Could not determine pagination cursor.");
    }

    if (lastReturnedTime === fromTime) {
      throw new Error(
        `Pagination stopped because OANDA returned the same timestamp twice: ${fromTime}`
      );
    }

    // Start from the last returned candle.
    // includeFirst=true lets us safely detect duplicates.
    fromTime = lastReturnedTime;

    if (candles.length < 5000) {
      console.log(
        "OANDA returned fewer than 5,000 candles; checking whether the period is complete."
      );

      // Fetching another page is still safe. The loop will stop when
      // OANDA returns no more data or reaches END_DATE.
    }
  }

  fs.renameSync(TEMP_FILE, OUTPUT_FILE);

  console.log("");
  console.log("Download complete.");
  console.log(`Total candles written: ${totalWritten}`);
  console.log(`Output file: ${OUTPUT_FILE}`);
}

main().catch((error) => {
  console.error("");
  console.error("Download failed:");
  console.error(error.message);

  if (fs.existsSync(TEMP_FILE)) {
    console.error(`Partial file preserved at: ${TEMP_FILE}`);
  }

  process.exitCode = 1;
});