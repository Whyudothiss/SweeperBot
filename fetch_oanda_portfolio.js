/**
 * Resumable OANDA candle downloader for the five-asset strategy portfolio.
 *
 * Signal / execution datasets:
 *   XAUUSD: M5 + M1
 *   BTCUSD: H1 + M1
 *   SPX:    M15 + M1
 *   NDX:    M15 + M1
 *   GBPUSD: M15 + M1
 *
 * OANDA instrument availability depends on the account's regulatory division.
 * Use --list-instruments to confirm the exact names available to your account.
 *
 * Examples:
 *   node fetch_oanda_portfolio.js --list-instruments
 *   node fetch_oanda_portfolio.js --preset btcusd
 *   node fetch_oanda_portfolio.js --preset portfolio
 *   node fetch_oanda_portfolio.js --instrument GBP_USD --granularity M15
 */

const fs = require("fs");
const path = require("path");

const TOKEN = process.env.OANDA_API_TOKEN;
const ACCOUNT_ID = process.env.OANDA_ACCOUNT_ID;
const ENVIRONMENT = process.env.OANDA_ENV || "practice";
const API_HOST = ENVIRONMENT === "live"
  ? "https://api-fxtrade.oanda.com"
  : "https://api-fxpractice.oanda.com";

const PRESETS = {
  xauusd: { instrument: "XAU_USD", granularities: ["M5", "M1"] },
  btcusd: { instrument: "BTC_USD", granularities: ["H1", "M1"] },
  spx: { instrument: "SPX500_USD", granularities: ["M15", "M1"] },
  ndx: { instrument: "NAS100_USD", granularities: ["M15", "M1"] },
  gbpusd: { instrument: "GBP_USD", granularities: ["M15", "M1"] },
};

function parseArgs(argv) {
  const options = {
    start: "2016-01-01T00:00:00Z",
    end: "2026-06-01T00:00:00Z",
    price: "M",
    outputDir: path.join(__dirname, "raw_data", "oanda"),
    overwrite: false,
    listInstruments: false,
  };

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--overwrite") options.overwrite = true;
    else if (argument === "--list-instruments") options.listInstruments = true;
    else if (argument === "--preset") options.preset = argv[++index]?.toLowerCase();
    else if (argument === "--instrument") options.instrument = argv[++index]?.toUpperCase();
    else if (argument === "--granularity") options.granularity = argv[++index]?.toUpperCase();
    else if (argument === "--start") options.start = argv[++index];
    else if (argument === "--end") options.end = argv[++index];
    else if (argument === "--price") options.price = argv[++index]?.toUpperCase();
    else if (argument === "--output-dir") options.outputDir = path.resolve(argv[++index]);
    else if (argument === "--help" || argument === "-h") options.help = true;
    else throw new Error(`Unknown argument: ${argument}`);
  }
  return options;
}

function printHelp() {
  console.log(`Usage:
  node fetch_oanda_portfolio.js --list-instruments
  node fetch_oanda_portfolio.js --preset <xauusd|btcusd|spx|ndx|gbpusd|portfolio>
  node fetch_oanda_portfolio.js --instrument <NAME> --granularity <M1|M5|M15|H1>

Options:
  --start <RFC3339>       Inclusive start (default 2016-01-01)
  --end <RFC3339>         Exclusive end (default 2026-06-01)
  --price <M|B|A|MBA>     OANDA price component (default M)
  --output-dir <PATH>     Default raw_data/oanda
  --overwrite             Replace a completed output file

Required environment:
  OANDA_API_TOKEN
  OANDA_ACCOUNT_ID        Also required for --list-instruments
  OANDA_ENV               practice (default) or live`);
}

function requireValue(value, name) {
  if (!value) throw new Error(`Missing ${name}.`);
  return value;
}

function normalizeTimestamp(timestamp) {
  return timestamp.replace(/\.(\d{3})\d+Z$/, ".$1Z");
}

function timestampMs(timestamp) {
  return Date.parse(normalizeTimestamp(timestamp));
}

function compactDate(timestamp) {
  const milliseconds = timestampMs(timestamp);
  if (!Number.isFinite(milliseconds)) throw new Error(`Invalid date/time: ${timestamp}`);
  return new Date(milliseconds).toISOString().slice(0, 10).replaceAll("-", "");
}

function csv(value) {
  const text = String(value ?? "");
  return /[,"\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function lastDataTimestamp(filePath) {
  if (!fs.existsSync(filePath)) return null;
  const size = fs.statSync(filePath).size;
  if (size === 0) return null;
  const length = Math.min(size, 64 * 1024);
  const buffer = Buffer.alloc(length);
  const descriptor = fs.openSync(filePath, "r");
  try {
    fs.readSync(descriptor, buffer, 0, length, size - length);
  } finally {
    fs.closeSync(descriptor);
  }
  const lines = buffer.toString("utf8").trim().split("\n");
  const last = lines.at(-1);
  if (!last || last.startsWith("timestamp,")) return null;
  return last.split(",", 1)[0];
}

async function oandaJson(url) {
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${TOKEN}`, Accept: "application/json" },
  });
  const body = await response.text();
  if (!response.ok) throw new Error(`OANDA HTTP ${response.status}: ${body}`);
  return JSON.parse(body);
}

async function listInstruments() {
  requireValue(ACCOUNT_ID, "OANDA_ACCOUNT_ID environment variable");
  const url = `${API_HOST}/v3/accounts/${encodeURIComponent(ACCOUNT_ID)}/instruments`;
  const result = await oandaJson(url);
  const instruments = (result.instruments || [])
    .map(({ name, displayName, type }) => ({ name, displayName, type }))
    .sort((left, right) => left.name.localeCompare(right.name));
  console.table(instruments);
  console.log(`Available instruments: ${instruments.length}`);
}

function tasksFor(options) {
  if (options.instrument || options.granularity) {
    return [{
      instrument: requireValue(options.instrument, "--instrument"),
      granularity: requireValue(options.granularity, "--granularity"),
    }];
  }
  const preset = requireValue(options.preset, "--preset");
  const names = preset === "portfolio" ? Object.keys(PRESETS) : [preset];
  return names.flatMap((name) => {
    const config = PRESETS[name];
    if (!config) throw new Error(`Unknown preset: ${name}`);
    return config.granularities.map((granularity) => ({ instrument: config.instrument, granularity }));
  });
}

function candleRow(candle) {
  const mid = candle.mid;
  return [candle.time, mid.o, mid.h, mid.l, mid.c, candle.volume].map(csv).join(",");
}

async function download({ instrument, granularity }, options) {
  const startMs = timestampMs(options.start);
  const endMs = timestampMs(options.end);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || startMs >= endMs) {
    throw new Error("--start and --end must be valid and start must precede end.");
  }
  if (options.price !== "M") {
    throw new Error("This pipeline currently requires --price M. Bid/ask storage needs a wider CSV schema.");
  }

  fs.mkdirSync(options.outputDir, { recursive: true });
  const filename = `${instrument}_${granularity}_${compactDate(options.start)}_${compactDate(options.end)}.csv`;
  const outputPath = path.join(options.outputDir, filename);
  const partPath = `${outputPath}.part`;

  if (fs.existsSync(outputPath) && !options.overwrite) {
    console.log(`Skipping completed file: ${outputPath}`);
    return;
  }
  if (options.overwrite) {
    if (fs.existsSync(outputPath)) fs.unlinkSync(outputPath);
    if (fs.existsSync(partPath)) fs.unlinkSync(partPath);
  }
  if (!fs.existsSync(partPath)) {
    fs.writeFileSync(partPath, "timestamp,open,high,low,close,volume\n");
  }

  const resumedAt = lastDataTimestamp(partPath);
  let cursor = resumedAt || options.start;
  let includeFirst = resumedAt ? "false" : "true";
  let written = 0;
  let page = 0;
  console.log(`\n${instrument} ${granularity}: ${resumedAt ? `resuming after ${resumedAt}` : `starting at ${options.start}`}`);

  while (true) {
    page += 1;
    const url = new URL(`${API_HOST}/v3/instruments/${instrument}/candles`);
    url.searchParams.set("granularity", granularity);
    url.searchParams.set("price", options.price);
    url.searchParams.set("count", "5000");
    url.searchParams.set("from", cursor);
    url.searchParams.set("includeFirst", includeFirst);
    const result = await oandaJson(url);
    const candles = result.candles || [];
    if (!candles.length) break;

    let lastReturned = null;
    let reachedEnd = false;
    const rows = [];
    for (const candle of candles) {
      lastReturned = candle.time;
      const candleTime = timestampMs(candle.time);
      if (!Number.isFinite(candleTime)) throw new Error(`Invalid timestamp returned by OANDA: ${candle.time}`);
      if (candleTime >= endMs) {
        reachedEnd = true;
        break;
      }
      if (candle.complete && candle.mid) rows.push(candleRow(candle));
    }
    if (rows.length) {
      fs.appendFileSync(partPath, `${rows.join("\n")}\n`);
      written += rows.length;
    }
    console.log(`  page ${page}: received ${candles.length}, appended ${rows.length}, this run ${written}`);
    if (reachedEnd) break;
    if (!lastReturned || lastReturned === cursor) throw new Error(`Pagination stalled at ${cursor}`);
    cursor = lastReturned;
    includeFirst = "false";
  }

  fs.renameSync(partPath, outputPath);
  console.log(`Saved: ${outputPath}`);
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    printHelp();
    return;
  }
  requireValue(TOKEN, "OANDA_API_TOKEN environment variable");
  if (options.listInstruments) {
    await listInstruments();
    return;
  }
  for (const task of tasksFor(options)) await download(task, options);
}

main().catch((error) => {
  console.error(`\nDownload failed: ${error.message}`);
  console.error("Any .part file is preserved; rerun the same command to resume.");
  process.exitCode = 1;
});
