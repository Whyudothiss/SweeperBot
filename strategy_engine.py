"""
Liquidity Sweep + Break of Structure strategy engine.

Design:
  - Signal generation (swing points, sweep, BOS) runs on the SIGNAL_TIMEFRAME
    (e.g. 5-min bars).
  - Trade management (did we get stopped out / trailed out, and exactly when)
    runs on 1-MIN bars, because within a single 5-min candle you cannot tell
    the order in which the stop and any favorable move occurred.
  - Stops are TOUCH-based (like a real stop order), not close-based.
  - No look-ahead: a swing point only becomes usable `lookforward` bars after
    it forms, matching how you'd actually be able to detect it live.

Inputs expected:
  signal_df: DataFrame with columns [datetime_utc, open, high, low, close, is_suspect]
             at your chosen signal timeframe (e.g. 5-min), sorted ascending.
  exec_df:   DataFrame with the same columns, at 1-min resolution, covering the
             same period, sorted ascending. Used only for trade simulation.

Both should already be cleaned (see clean_and_merge.py) — this engine will
simply skip any row flagged is_suspect during swing detection.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, List


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class StrategyConfig:
    lookback: int = 2              # candles to the left required to confirm a swing
    lookforward: int = 1           # candles to the right required to confirm a swing
    atr_period: int = 14
    stop_buffer_atr_mult: float = 0.15   # buffer beyond sweep wick, in units of ATR
    trail_activation_r: float = 1.0      # start trailing once unrealized profit >= this many R
    base_capital: float = 10_000.0
    risk_pct: float = 0.005              # 0.5% of current equity per trade
    direction: str = "both"              # "long", "short", or "both"


@dataclass
class Trade:
    entry_time: pd.Timestamp
    direction: str  # "long" or "short"
    entry_price: float
    initial_stop: float
    sweep_extreme: float
    size: float
    risk_dollars: float
    exit_time: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    r_multiple: Optional[float] = None
    pnl_dollars: Optional[float] = None


# ---------------------------------------------------------------------------
# ATR (used for the stop buffer)
# ---------------------------------------------------------------------------

def compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ---------------------------------------------------------------------------
# Swing detection (no look-ahead: confirmed at index + lookforward)
# ---------------------------------------------------------------------------

def detect_swings(df: pd.DataFrame, lookback: int, lookforward: int) -> pd.DataFrame:
    """
    Adds columns:
      is_swing_high_raw / is_swing_low_raw : True at the PEAK bar itself
      swing_confirmed_at                    : index at which this swing becomes
                                               knowable (peak_index + lookforward)
    A swing point must only be referenced by signal logic from its
    swing_confirmed_at index onward, never from the peak index itself.
    """
    n = len(df)
    highs = df["high"].values
    lows = df["low"].values
    usable = ~df["is_suspect"].values if "is_suspect" in df.columns else np.ones(n, dtype=bool)

    is_swing_high = np.zeros(n, dtype=bool)
    is_swing_low = np.zeros(n, dtype=bool)

    for i in range(lookback, n - lookforward):
        if not usable[i]:
            continue
        left = highs[i - lookback:i]
        right = highs[i + 1:i + 1 + lookforward]
        if len(left) == lookback and len(right) == lookforward:
            if highs[i] > left.max() and highs[i] > right.max():
                is_swing_high[i] = True

        left_l = lows[i - lookback:i]
        right_l = lows[i + 1:i + 1 + lookforward]
        if len(left_l) == lookback and len(right_l) == lookforward:
            if lows[i] < left_l.min() and lows[i] < right_l.min():
                is_swing_low[i] = True

    df = df.copy()
    df["is_swing_high_raw"] = is_swing_high
    df["is_swing_low_raw"] = is_swing_low
    df["swing_confirmed_idx"] = np.where(
        is_swing_high | is_swing_low,
        df.index.values + lookforward,
        -1,
    )
    return df


# ---------------------------------------------------------------------------
# Signal generation: sweep + break of structure
# ---------------------------------------------------------------------------

def generate_signals(df: pd.DataFrame, cfg: StrategyConfig) -> List[dict]:
    """
    Walks forward bar by bar (index order = time order). At each bar:
      - only swings confirmed as of THIS bar (swing_confirmed_idx <= current i)
        are eligible to be referenced.
      - tracks the most recent unswept confirmed swing high / low.
      - detects a sweep: current bar's high/low exceeds the tracked level,
        and the SAME or a LATER bar closes back inside it (rejection).
      - after a sweep, watches for a break of structure through the most
        recent confirmed swing on the OPPOSITE side -> that's the entry.
    Returns a list of signal dicts: {time, direction, entry_price, sweep_extreme}
    """
    n = len(df)
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    times = df["datetime_utc"].values

    # pools of confirmed-and-not-yet-swept swing levels, most recent first
    recent_swing_high = None  # (price, index)
    recent_swing_low = None

    pending_sweep = None  # dict describing an in-progress sweep waiting for BOS

    signals = []

    for i in range(n):
        # activate any swing confirmed as of this bar
        if df["is_swing_high_raw"].iat[i - cfg.lookforward] if i - cfg.lookforward >= 0 else False:
            pass  # handled via confirmed_idx pass below

        # bring in newly confirmed swings (confirmed_idx == i means usable from here)
        confirmed_now = df.index[df["swing_confirmed_idx"] == i]
        for ci in confirmed_now:
            if df["is_swing_high_raw"].iat[ci]:
                recent_swing_high = (highs[ci], ci)
            if df["is_swing_low_raw"].iat[ci]:
                recent_swing_low = (lows[ci], ci)

        # --- check for sweep of the recent swing high (-> potential short) ---
        if recent_swing_high is not None and pending_sweep is None:
            level, _ = recent_swing_high
            if highs[i] > level and closes[i] < level:
                # swept and rejected back below in the same bar
                pending_sweep = {"direction": "short", "extreme": highs[i], "swept_idx": i}

        if recent_swing_low is not None and pending_sweep is None:
            level, _ = recent_swing_low
            if lows[i] < level and closes[i] > level:
                pending_sweep = {"direction": "long", "extreme": lows[i], "swept_idx": i}

        # --- if we have a pending sweep, watch for BOS confirmation ---
        if pending_sweep is not None and i > pending_sweep["swept_idx"]:
            if pending_sweep["direction"] == "short" and recent_swing_low is not None:
                bos_level, _ = recent_swing_low
                if closes[i] < bos_level:
                    if cfg.direction in ("both", "short"):
                        signals.append({
                            "time": times[i],
                            "index": i,
                            "direction": "short",
                            "entry_price": closes[i],
                            "sweep_extreme": pending_sweep["extreme"],
                        })
                    pending_sweep = None
                    recent_swing_high = None  # that liquidity has been used

            elif pending_sweep["direction"] == "long" and recent_swing_high is not None:
                bos_level, _ = recent_swing_high
                if closes[i] > bos_level:
                    if cfg.direction in ("both", "long"):
                        signals.append({
                            "time": times[i],
                            "index": i,
                            "direction": "long",
                            "entry_price": closes[i],
                            "sweep_extreme": pending_sweep["extreme"],
                        })
                    pending_sweep = None
                    recent_swing_low = None

    return signals


# ---------------------------------------------------------------------------
# Trade simulation on 1-min data (touch-based stop, structure trailing)
# ---------------------------------------------------------------------------

def simulate_trade(
    signal: dict,
    exec_df: pd.DataFrame,
    atr_at_signal: float,
    equity: float,
    cfg: StrategyConfig,
) -> Optional[Trade]:
    direction = signal["direction"]
    entry_time = pd.Timestamp(signal["time"])
    if entry_time.tzinfo is None:
        entry_time = entry_time.tz_localize("UTC")
    entry_price = signal["entry_price"]
    sweep_extreme = signal["sweep_extreme"]

    buffer = cfg.stop_buffer_atr_mult * atr_at_signal
    if direction == "long":
        initial_stop = sweep_extreme - buffer
    else:
        initial_stop = sweep_extreme + buffer

    stop_distance = abs(entry_price - initial_stop)
    if stop_distance <= 0 or np.isnan(stop_distance):
        return None  # degenerate, skip

    risk_dollars = equity * cfg.risk_pct
    size = risk_dollars / stop_distance

    trade = Trade(
        entry_time=entry_time,
        direction=direction,
        entry_price=entry_price,
        initial_stop=initial_stop,
        sweep_extreme=sweep_extreme,
        size=size,
        risk_dollars=risk_dollars,
    )

    # walk forward on 1-min bars from entry_time onward
    exec_slice = exec_df[exec_df["datetime_utc"] >= entry_time]
    if exec_slice.empty:
        return None

    current_stop = initial_stop
    trailing_active = False
    r_one_distance = stop_distance  # 1R = initial risk distance

    exec_highs = exec_slice["high"].values
    exec_lows = exec_slice["low"].values
    exec_times = exec_slice["datetime_utc"].values

    for j in range(len(exec_slice)):
        bar_high = exec_highs[j]
        bar_low = exec_lows[j]
        bar_time = exec_times[j]

        if direction == "long":
            # stop check first (touch-based, worst-case-first ordering within bar)
            if bar_low <= current_stop:
                trade.exit_time = pd.Timestamp(bar_time)
                trade.exit_price = current_stop
                trade.exit_reason = "trail_stop" if trailing_active else "initial_stop"
                break

            # activate trailing once 1R reached
            unrealized_r = (bar_high - entry_price) / r_one_distance
            if not trailing_active and unrealized_r >= cfg.trail_activation_r:
                trailing_active = True

            if trailing_active:
                # simple structure-free trail: lock in stop at
                # (current best favorable price - r_one_distance), never moving down
                candidate_stop = bar_high - r_one_distance
                if candidate_stop > current_stop:
                    current_stop = candidate_stop

        else:  # short
            if bar_high >= current_stop:
                trade.exit_time = pd.Timestamp(bar_time)
                trade.exit_price = current_stop
                trade.exit_reason = "trail_stop" if trailing_active else "initial_stop"
                break

            unrealized_r = (entry_price - bar_low) / r_one_distance
            if not trailing_active and unrealized_r >= cfg.trail_activation_r:
                trailing_active = True

            if trailing_active:
                candidate_stop = bar_low + r_one_distance
                if candidate_stop < current_stop:
                    current_stop = candidate_stop

    else:
        # ran off the end of available data without being stopped out
        trade.exit_time = pd.Timestamp(exec_times[-1])
        trade.exit_price = exec_slice["close"].iat[-1]
        trade.exit_reason = "data_end"

    if direction == "long":
        trade.pnl_dollars = (trade.exit_price - entry_price) * size
    else:
        trade.pnl_dollars = (entry_price - trade.exit_price) * size

    trade.r_multiple = trade.pnl_dollars / risk_dollars
    return trade


# ---------------------------------------------------------------------------
# Full backtest loop
# ---------------------------------------------------------------------------

def run_backtest(signal_df: pd.DataFrame, exec_df: pd.DataFrame, cfg: StrategyConfig):
    signal_df = signal_df.reset_index(drop=True)
    signal_df = detect_swings(signal_df, cfg.lookback, cfg.lookforward)
    signal_df["atr"] = compute_atr(signal_df, cfg.atr_period)

    signals = generate_signals(signal_df, cfg)

    equity = cfg.base_capital
    trades: List[Trade] = []
    open_trade_active_until = None  # simplistic: one trade at a time

    for sig in signals:
        if open_trade_active_until is not None and pd.Timestamp(sig["time"]) < open_trade_active_until:
            continue  # skip overlapping signals; only one position at a time

        atr_val = signal_df["atr"].iat[sig["index"]]
        if pd.isna(atr_val):
            continue

        trade = simulate_trade(sig, exec_df, atr_val, equity, cfg)
        if trade is None:
            continue

        equity += trade.pnl_dollars
        trades.append(trade)
        open_trade_active_until = trade.exit_time

    return trades, equity


def summarize(trades: List[Trade], base_capital: float, final_equity: float):
    if not trades:
        print("No trades generated.")
        return
    n = len(trades)
    wins = [t for t in trades if t.pnl_dollars > 0]
    total_return_pct = (final_equity - base_capital) / base_capital * 100
    avg_r = np.mean([t.r_multiple for t in trades])
    print(f"Trades: {n}")
    print(f"Win rate: {len(wins)/n*100:.1f}%")
    print(f"Avg R multiple: {avg_r:.2f}")
    print(f"Final equity: {final_equity:.2f}  (from {base_capital:.2f})")
    print(f"Total return: {total_return_pct:.2f}%")
