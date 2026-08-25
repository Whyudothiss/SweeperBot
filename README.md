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

| Asset   | Direction     | Timeframe | Session Mode   | Exit           |
|---------|---------------|-----------|-----------------|----------------|
| XAUUSD  | Long + short  | M5        | No session      | Trailing stop  |
| BTCUSD  | Long + short  | H1        | No session      | Trailing stop  |
| SPX     | Long only     | M15       | Asset session   | Trailing stop  |
| NDX     | Long only     | M15       | Asset session   | Trailing stop  |
| GBPUSD  | Long + short  | M15       | Asset session   | Fixed 1R exit  |

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

Signals (swing detection, sweep, break of structure) are generated on the **signal timeframe**
(5-min for XAUUSD). Trade management (did the stop or trailing stop actually get hit, and in
what order relative to any favorable move) is simulated on **1-minute data**, because within a
single 5-min candle you cannot tell from OHLC alone which level was touched first if both the
stop and a favorable move occurred within that same candle. Stops are touch-based (execute the
instant price reaches the level), matching how a real stop order behaves, not close-based.

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

## Honest framing

This is explicitly a simplified, more carefully risk-managed version of a publicly-taught
retail strategy that the source video found did not hold up as a full system. The goal here
is not to assume the underlying "smart money" narrative is true, but to test a specific,
well-defined mechanical version of it with disciplined data handling and without the
overfitting shortcuts the original test flagged in itself.