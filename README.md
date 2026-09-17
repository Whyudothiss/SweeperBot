# Liquidity Sweep Backtest

## Background

This project is inspired by the "smart money concepts" liquidity-sweep-plus-break-of-structure
strategy popularized by TJR, and specifically by a YouTube deep-dive that coded up TJR's full
publicly-taught framework (10,000+ lines, ~300 videos reviewed) and backtested it across
forex pairs over 10 years. That video's key finding: the *full* framework (sweep + BOS +
retracement entry + daily/weekly bias filter) actually performed worse the more rules were
stacked on top of each other. The simplest version — sweep, break of structure, enter — was
the only version that stayed net profitable, and even then only modestly (~20% over 10 years,
well behind buy-and-hold).

This project does **not** attempt to reproduce that full framework. Instead, it strips the
idea down to its simplest form and rebuilds it more carefully:

- Liquidity sweep (price takes out a prior swing high/low, then rejects back)
- Break of structure (price then breaks the opposing recent swing) as the entry trigger
- No retracement/FVG/order-block entry filter
- No higher-timeframe bias filter
- A trailing stop instead of a fixed 3-way take-profit split, to avoid the partial-scaling
  payoff decay the original video's data exposed (where hitting all 3 TPs only nets ~2R
  total, despite the individual RR ratios looking like 1:1, 1:2, 1:3)

## Why start with XAUUSD

We're testing on **XAUUSD (gold), 5-minute timeframe, no session filter** first, before
expanding to other assets. Reasons:

1. Gold trades continuously (no session gaps to account for), which simplifies the data
   pipeline and removes one variable while we validate the core logic.
2. It gives us a large, clean sample size to properly search for the best swing-detection
   ratio (see below) before adding the complexity of session-gated assets (SPX, NDX, GBPUSD).
3. Once the approach is validated and the ratio is chosen with a defensible methodology, the
   same engine gets pointed at the other assets/timeframes/session rules from the target
   config (see `v1_setup` table below).

## Target multi-asset config (future scope, after XAUUSD is validated)

| Asset   | Direction     | Timeframe | Session Mode   | Exit           | Risk/trade |
|---------|---------------|-----------|----------------|----------------|------------|
| XAUUSD  | Long + short  | M5        | No session     | Trailing stop  | 0.5%       |
| BTCUSD  | Long + short  | H1        | No session     | Trailing stop  | 2.0%       |
| SPX     | Long only     | M15       | Asset session  | Trailing stop  | 1.5%       |
| NDX     | Long only     | M15       | Asset session  | Trailing stop  | 1.5%       |
| GBPUSD  | Long + short  | M15       | Asset session  | Fixed 1R exit  | 1.5%       |

## The swing lookback/lookforward ratio problem

A swing high/low is defined by how many candles to the left (`lookback`) and right
(`lookforward`) must be lower/higher than the candidate peak/trough for it to count as a
confirmed swing point. This single choice determines what counts as "structure" at all —
tight parameters (e.g. 2,1) catch every minor wiggle and produce many noisy signals; loose
parameters (e.g. 12,6) only catch major turns and produce few, more significant signals.

The video this project is inspired by picked its ratio by testing several values and keeping
whichever performed best — explicitly flagged in the video itself as in-sample optimization /
overfitting, done deliberately so that a still-failing strategy couldn't blame the parameter
choice. We're aiming to do this more rigorously: sweep a grid of (lookback, lookforward) pairs
on XAUUSD, and evaluate them with an eye toward robustness across the sweep (e.g. a broad
plateau of decent-performing neighboring parameters is more trustworthy than a single sharp
spike), ideally validated on a held-out period rather than picking the single best in-sample
result.

## Look-ahead bias handling

A swing point cannot be known to be a confirmed swing until `lookforward` candles after it
have closed. The engine only allows a swing point to be referenced by signal logic starting
from `peak_index + lookforward`, never from the peak candle itself — this is the most common
source of inflated backtest performance in DIY liquidity-sweep scripts and is enforced
explicitly in `strategy_engine.py`.

## Two-timeframe design: signal vs. execution

Swings and sweep rejection are generated on the **signal timeframe** (5-min for XAUUSD). After
a sweep candle closes back inside its swept level, the opposing confirmed swing price becomes
a stop-style BOS entry. The first **1-minute** candle to touch that price supplies the entry
time; if it gaps through, the fill uses the M1 open. The initial benchmark stop is exactly at
the sweep wick (zero ATR buffer). Trade management also uses M1 data. Stops are touch-based,
and any entry-minute or trailing-stop ordering that M1 OHLC cannot resolve is handled
conservatively.

The source transcript also mentions a volatility filter that skips quiet/dead markets, but it
does not disclose a reproducible formula or threshold. That filter is therefore not included;
adding an invented threshold would make comparison less honest, not more accurate.

## Trade viewer

The viewer defaults to OANDA XAUUSD M5/M1, `(18,6)`, 0.5% risk, and an exact-wick stop. This
ratio most closely matched the source video's gold trade count in the current parity check.
Override any of these without editing code:

```bash
cd trade-viewer
SWEEPER_LOOKBACK=13 SWEEPER_LOOKFORWARD=3 npm run dev
```

Optional overrides are `SWEEPER_SIGNAL_PATH`, `SWEEPER_EXEC_PATH`, `SWEEPER_RISK_PCT`, and
`SWEEPER_STOP_BUFFER_ATR_MULT`.

## Pipeline

1. `fetch_xauusd.js` — pulls XAUUSD candles from Dukascopy (free, no API key) year by year.
   Run once with `TIMEFRAME = "m5"` and once with `TIMEFRAME = "m1"`.
2. `clean_and_merge.py` — merges the raw per-year files into one master CSV per timeframe.
   Never fabricates missing/bad data — flags it instead (`is_bad_numeric`, `is_ohlc_invalid`,
   `is_flat_candle`, `is_weekend_gap`, `is_short_data_gap`, rollup `is_suspect`) so the
   strategy engine can skip suspect candles during swing detection rather than treating
   invented values as real price action.
3. `strategy_engine.py` — swing detection, sweep + BOS signal generation, and 1-min-resolution
   trade simulation with a trailing stop, sized at 0.5% equity risk per trade.
4. (Next) Ratio sweep script — runs the engine across a grid of (lookback, lookforward) pairs
   on XAUUSD 5-min and scores each combination.
5. (Later) Extend to BTCUSD, SPX, NDX, GBPUSD with their respective timeframes, session
   filters, and directional restrictions per the target config table above.

## OANDA five-asset data

The configurable OANDA downloader covers the five assets described in the
second video and downloads both the signal timeframe and M1 execution data.
Instrument availability varies by OANDA account division, so list the exact
instruments available to your account before downloading:

```bash
export OANDA_API_TOKEN="your-token"
export OANDA_ENV="practice"
node fetch_oanda_portfolio.js --list-instruments
```

You may instead place those values in the gitignored project `.env` file;
explicit shell environment variables take precedence. `OANDA_ACCOUNT_ID` is
optional when the token exposes exactly one account; otherwise add it to select
the intended account.

The built-in candidates are `XAU_USD`, `BTC_USD`, `SPX500_USD`, `NAS100_USD`,
and `GBP_USD`. If those names are available, download the January 2015 through
December 2025 study period. Interrupted downloads retain a `.part` file and resume
when the same command is run again.

```bash
node fetch_oanda_portfolio.js --preset portfolio
python3 prepare_oanda_portfolio.py --preset portfolio
```

If the XAUUSD Parquets already exist, fetch and prepare only the other four assets:

```bash
node fetch_oanda_portfolio.js --preset remaining
python3 prepare_oanda_portfolio.py --preset remaining
python3 validate_oanda_portfolio.py
```

The preparation step writes these Parquet pairs under `data/processed`:

| Asset | Signal | Execution |
|---|---|---|
| XAUUSD | `oanda_xauusd_m5_master.parquet` | `oanda_xauusd_m1_master.parquet` |
| BTCUSD | `oanda_btcusd_h1_master.parquet` | `oanda_btcusd_m1_master.parquet` |
| SPX | `oanda_spx_m15_master.parquet` | `oanda_spx_m1_master.parquet` |
| NDX | `oanda_ndx_m15_master.parquet` | `oanda_ndx_m1_master.parquet` |
| GBPUSD | `oanda_gbpusd_m15_master.parquet` | `oanda_gbpusd_m1_master.parquet` |

## Honest framing

This is explicitly a simplified, more carefully risk-managed version of a publicly-taught
retail strategy that the source video found did not hold up as a full system. The goal here
is not to assume the underlying "smart money" narrative is true, but to test a specific,
well-defined mechanical version of it with disciplined data handling and without the
overfitting shortcuts the original test flagged in itself.
