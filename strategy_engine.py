"""
Liquidity Sweep + Break of Structure strategy engine.

Design:
  - Signal generation (swing points, sweep, BOS) runs on the SIGNAL_TIMEFRAME
    (e.g. 5-min bars).
  - A BOS arms a stop-style entry at the broken swing price.  The first
    1-MIN bar that reaches that price supplies the fill time (and the open if
    price gaps through the level).
  - Trade management (did we get stopped out / trailed out, and exactly when)
    also runs on 1-MIN bars, because within a single signal candle you cannot
    tell the order in which the stop and any favorable move occurred.
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


def _as_utc_timestamp(value) -> pd.Timestamp:
    """Normalize numpy/pandas timestamps to timezone-aware UTC."""
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class StrategyConfig:
    lookback: int = 2              # candles to the left required to confirm a swing
    lookforward: int = 1           # candles to the right required to confirm a swing
    atr_period: int = 14
    stop_buffer_atr_mult: float = 0.0    # benchmark: stop exactly at sweep wick; optionally add ATR units
    trail_activation_r: float = 1.0      # start trailing once unrealized profit >= this many R
    max_pending_bars: int = 100          # invalidate a pending sweep if BOS doesn't confirm within this many signal-timeframe bars
    max_rejection_wait_bars: int = 0    # invalidate a wick-through-level watch if it never closes back inside within this many bars
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
    bos_index: Optional[int] = None
    fill_index: Optional[int] = None
    swept_level: Optional[float] = None
    swept_swing_index: Optional[int] = None
    sweep_extreme_index: Optional[int] = None
    sweep_confirm_index: Optional[int] = None
    # The opposite-side swing whose price triggers the BOS entry.  Keeping
    # this lets a replay show exactly what structure was broken.
    bos_level: Optional[float] = None
    bos_swing_index: Optional[int] = None
    exit_time: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    r_multiple: Optional[float] = None
    pnl_dollars: Optional[float] = None
    # Number of 1-minute OHLC bars where the high/low ordering could change
    # the trailing-stop result.  The simulator takes the conservative exit.
    ambiguous_intrabar_events: int = 0
    ambiguous_entry_bar: bool = False


# ---------------------------------------------------------------------------
# ATR (used for the stop buffer)
# ---------------------------------------------------------------------------

def compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr_with_gap = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    # For candles immediately after a weekend/data gap, the prev_close jump
    # is not real intra-session volatility -- fall back to just high-low so
    # a weekend gap doesn't inflate the ATR-based stop buffer.
    if "is_weekend_gap" in df.columns or "is_short_data_gap" in df.columns:
        gap_mask = pd.Series(False, index=df.index)
        if "is_weekend_gap" in df.columns:
            gap_mask = gap_mask | df["is_weekend_gap"].fillna(False)
        if "is_short_data_gap" in df.columns:
            gap_mask = gap_mask | df["is_short_data_gap"].fillna(False)
        tr = tr_with_gap.where(~gap_mask, high - low)
    else:
        tr = tr_with_gap

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
    if "is_weekend_gap" in df.columns:
        usable = usable & ~df["is_weekend_gap"].fillna(False).values
    if "is_short_data_gap" in df.columns:
        usable = usable & ~df["is_short_data_gap"].fillna(False).values

    is_swing_high = np.zeros(n, dtype=bool)
    is_swing_low = np.zeros(n, dtype=bool)

    for i in range(lookback, n - lookforward):
        # A swing needs a clean, continuous comparison window.  It is not
        # enough for only the candidate candle to be usable: otherwise a
        # flat/suspect candle or a gap beside it can silently define the
        # structure level.
        if not usable[i - lookback:i + lookforward + 1].all():
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
    Walks forward bar by bar (index order = time order). Tracks three states:

      1. No active setup: watching the most recent confirmed swing high/low
         for a wick beyond the level.
      2. Watching for rejection: a wick has broken the level, but the close
         hasn't come back inside yet -- this can span multiple bars. The
         tracked "extreme" keeps updating to the furthest wick reached while
         watching, in case price pushes further before rejecting. If the
         close never comes back inside within max_rejection_wait_bars, the
         watch is cancelled (treated as a genuine breakout, not a sweep).
      3. Confirmed sweep, watching for BOS: once the close comes back inside
         the level, arm an entry at the most recent confirmed swing on the
         OPPOSITE side. A later candle touching that price is a BOS trigger.
         The 1-minute executor determines the exact first-touch time. If BOS
         does not occur within max_pending_bars, the setup is cancelled.

    Returns signal dictionaries containing the BOS trigger window and level.
    """
    n = len(df)
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    times = df["datetime_utc"].values

    # pools of confirmed-and-not-yet-swept swing levels, most recent first
    recent_swing_high = None  # (price, index)
    recent_swing_low = None

    pending_watch = None   # {"direction", "level", "swing_idx", "extreme", "extreme_idx", "start_idx"} -- wick broke level, awaiting rejection close
    pending_sweep = None   # {"direction", "level", "swing_idx", "extreme", "sweep_extreme_idx", "swept_idx"} -- rejection confirmed, awaiting BOS

    signals = []

    # A direct lookup avoids scanning the entire DataFrame on every bar.
    # `swing_confirmed_idx` is calculated from the positional index above, so
    # this preserves the same no-look-ahead timing as the prior implementation.
    confirmed_by_idx = {}
    for candidate_idx, confirmed_idx in enumerate(df["swing_confirmed_idx"].values):
        if confirmed_idx >= 0:
            confirmed_by_idx.setdefault(int(confirmed_idx), []).append(candidate_idx)

    for i in range(n):
        # invalidate a pending sweep if BOS hasn't confirmed within the timeout window
        if pending_sweep is not None and (i - pending_sweep["swept_idx"]) > cfg.max_pending_bars:
            pending_sweep = None

        # invalidate a rejection watch if price never closed back inside the level
        if pending_watch is not None and (i - pending_watch["start_idx"]) > cfg.max_rejection_wait_bars:
            pending_watch = None

        # A later break beyond the rejection wick means the earlier sweep did
        # not hold.  It is no longer a valid reversal setup, so do not let it
        # wait for a BOS that happens much later.
        if pending_sweep is not None and i > pending_sweep["swept_idx"]:
            if (
                pending_sweep["direction"] == "short"
                and highs[i] > pending_sweep["extreme"]
            ) or (
                pending_sweep["direction"] == "long"
                and lows[i] < pending_sweep["extreme"]
            ):
                pending_sweep = None

        # bring in newly confirmed swings (confirmed_idx == i means usable from here)
        for ci in confirmed_by_idx.get(i, []):
            if df["is_swing_high_raw"].iat[ci]:
                recent_swing_high = (highs[ci], ci)
            if df["is_swing_low_raw"].iat[ci]:
                recent_swing_low = (lows[ci], ci)

        # --- start watching for a wick through the recent swing high (-> potential short) ---
        if pending_watch is None and pending_sweep is None and recent_swing_high is not None:
            level, swing_idx = recent_swing_high
            if highs[i] > level:
                pending_watch = {
                    "direction": "short", "level": level, "extreme": highs[i],
                    "swing_idx": swing_idx, "extreme_idx": i, "start_idx": i,
                }

        if pending_watch is None and pending_sweep is None and recent_swing_low is not None:
            level, swing_idx = recent_swing_low
            if lows[i] < level:
                pending_watch = {
                    "direction": "long", "level": level, "extreme": lows[i],
                    "swing_idx": swing_idx, "extreme_idx": i, "start_idx": i,
                }

        # --- while watching, update the extreme and check for the rejection close ---
        if pending_watch is not None:
            if pending_watch["direction"] == "short":
                if highs[i] > pending_watch["extreme"]:
                    pending_watch["extreme"] = highs[i]
                    pending_watch["extreme_idx"] = i
                if closes[i] < pending_watch["level"]:
                    pending_sweep = {
                        "direction": "short", "level": pending_watch["level"],
                        "swing_idx": pending_watch["swing_idx"], "extreme": pending_watch["extreme"],
                        "sweep_extreme_idx": pending_watch["extreme_idx"], "swept_idx": i,
                    }
                    pending_watch = None
            else:
                if lows[i] < pending_watch["extreme"]:
                    pending_watch["extreme"] = lows[i]
                    pending_watch["extreme_idx"] = i
                if closes[i] > pending_watch["level"]:
                    pending_sweep = {
                        "direction": "long", "level": pending_watch["level"],
                        "swing_idx": pending_watch["swing_idx"], "extreme": pending_watch["extreme"],
                        "sweep_extreme_idx": pending_watch["extreme_idx"], "swept_idx": i,
                    }
                    pending_watch = None

        # --- if we have a confirmed sweep, watch for a BOS price touch ---
        if pending_sweep is not None and i > pending_sweep["swept_idx"]:
            if pending_sweep["direction"] == "short" and recent_swing_low is not None:
                bos_level, bos_swing_index = recent_swing_low
                if lows[i] <= bos_level and i + 1 < n:
                    if cfg.direction in ("both", "short"):
                        signals.append({
                            "time": times[i],
                            "trigger_end_time": times[i + 1],
                            "index": i,
                            "fill_index": i,
                            "direction": "short",
                            "entry_price": bos_level,
                            "swept_level": pending_sweep["level"],
                            "swept_swing_index": pending_sweep["swing_idx"],
                            "sweep_extreme": pending_sweep["extreme"],
                            "sweep_extreme_index": pending_sweep["sweep_extreme_idx"],
                            "sweep_confirm_index": pending_sweep["swept_idx"],
                            "bos_level": bos_level,
                            "bos_swing_index": bos_swing_index,
                        })
                    pending_sweep = None
                    recent_swing_high = None  # that liquidity has been used

            elif pending_sweep["direction"] == "long" and recent_swing_high is not None:
                bos_level, bos_swing_index = recent_swing_high
                if highs[i] >= bos_level and i + 1 < n:
                    if cfg.direction in ("both", "long"):
                        signals.append({
                            "time": times[i],
                            "trigger_end_time": times[i + 1],
                            "index": i,
                            "fill_index": i,
                            "direction": "long",
                            "entry_price": bos_level,
                            "swept_level": pending_sweep["level"],
                            "swept_swing_index": pending_sweep["swing_idx"],
                            "sweep_extreme": pending_sweep["extreme"],
                            "sweep_extreme_index": pending_sweep["sweep_extreme_idx"],
                            "sweep_confirm_index": pending_sweep["swept_idx"],
                            "bos_level": bos_level,
                            "bos_swing_index": bos_swing_index,
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
    trigger_start = _as_utc_timestamp(signal["time"])
    trigger_end = _as_utc_timestamp(signal["trigger_end_time"])
    entry_level = signal["entry_price"]
    sweep_extreme = signal["sweep_extreme"]

    if cfg.stop_buffer_atr_mult == 0:
        buffer = 0.0
    elif not np.isfinite(atr_at_signal):
        return None
    else:
        buffer = cfg.stop_buffer_atr_mult * atr_at_signal
    if direction == "long":
        initial_stop = sweep_extreme - buffer
    else:
        initial_stop = sweep_extreme + buffer

    # Locate the first 1-minute bar that reaches the armed BOS level.  The
    # signal-candle OHLC proves that a touch happened somewhere, while M1
    # identifies when.  If the first M1 bar opens through the level, a real
    # stop order fills at that open rather than at an untraded price.
    trigger_start_idx = int(exec_df["datetime_utc"].searchsorted(trigger_start, side="left"))
    trigger_end_idx = int(exec_df["datetime_utc"].searchsorted(trigger_end, side="left"))
    if trigger_start_idx >= trigger_end_idx:
        return None

    exec_opens = exec_df["open"].values
    exec_highs = exec_df["high"].values
    exec_lows = exec_df["low"].values
    exec_closes = exec_df["close"].values
    exec_times = exec_df["datetime_utc"].values

    entry_idx = None
    entry_price = None
    for j in range(trigger_start_idx, trigger_end_idx):
        if direction == "long" and exec_highs[j] >= entry_level:
            entry_idx = j
            entry_price = max(float(entry_level), float(exec_opens[j]))
            break
        if direction == "short" and exec_lows[j] <= entry_level:
            entry_idx = j
            entry_price = min(float(entry_level), float(exec_opens[j]))
            break
    if entry_idx is None or entry_price is None:
        return None
    entry_time = _as_utc_timestamp(exec_times[entry_idx])

    # A stop must be on the loss side of the entry.  Using abs() here used to
    # turn a nonsensical stop on the profitable side into an instant +1R win.
    if not np.isfinite(entry_price) or not np.isfinite(initial_stop):
        return None
    if direction == "long":
        if initial_stop >= entry_price:
            return None
        stop_distance = entry_price - initial_stop
    else:
        if initial_stop <= entry_price:
            return None
        stop_distance = initial_stop - entry_price

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
        bos_index=signal.get("index"),
        fill_index=signal.get("fill_index"),
        swept_level=signal.get("swept_level"),
        swept_swing_index=signal.get("swept_swing_index"),
        sweep_extreme_index=signal.get("sweep_extreme_index"),
        sweep_confirm_index=signal.get("sweep_confirm_index"),
        bos_level=signal.get("bos_level"),
        bos_swing_index=signal.get("bos_swing_index"),
    )

    current_stop = initial_stop
    trailing_active = False
    r_one_distance = stop_distance
    running_extreme = entry_price  # best price reached so far, for trailing purposes

    EPS = 1e-9  # guards against float noise landing just under a whole-R boundary

    for j in range(entry_idx, len(exec_df)):
        bar_high = exec_highs[j]
        bar_low = exec_lows[j]
        bar_time = exec_times[j]

        # OHLC cannot reveal whether the entry or the distant stop happened
        # first inside the entry minute.  Treat a bar containing both as an
        # immediate loss, the conservative assumption.
        if j == entry_idx and (
            (direction == "long" and bar_low <= initial_stop)
            or (direction == "short" and bar_high >= initial_stop)
        ):
            trade.ambiguous_entry_bar = True
            trade.ambiguous_intrabar_events += 1
            trade.exit_time = _as_utc_timestamp(bar_time)
            trade.exit_price = initial_stop
            trade.exit_reason = "initial_stop"
            break

        if direction == "long":
            if bar_high > running_extreme:
                running_extreme = bar_high
            achieved_r = (running_extreme - entry_price) / r_one_distance

            if achieved_r >= cfg.trail_activation_r:
                step_r = np.floor(achieved_r + EPS) - 1   # 1R->0 (BE), 2R->1R, 3R->2R...
                candidate_stop = max(current_stop, entry_price + step_r * r_one_distance)
            else:
                candidate_stop = current_stop

            stop_was_raised = candidate_stop > current_stop
            ambiguous = stop_was_raised and bar_low <= candidate_stop

            if bar_low <= current_stop:
                if ambiguous:
                    trade.ambiguous_intrabar_events += 1
                trade.exit_time = _as_utc_timestamp(bar_time)
                trade.exit_price = current_stop
                trade.exit_reason = "trail_stop" if trailing_active else "initial_stop"
                break

            trailing_active = trailing_active or (achieved_r >= cfg.trail_activation_r)
            current_stop = candidate_stop

            if ambiguous:
                trade.ambiguous_intrabar_events += 1
                trade.exit_time = _as_utc_timestamp(bar_time)
                trade.exit_price = current_stop
                trade.exit_reason = "trail_stop"
                break

        else:  # short
            if bar_low < running_extreme:
                running_extreme = bar_low
            achieved_r = (entry_price - running_extreme) / r_one_distance

            if achieved_r >= cfg.trail_activation_r:
                step_r = np.floor(achieved_r + EPS) - 1
                candidate_stop = min(current_stop, entry_price - step_r * r_one_distance)
            else:
                candidate_stop = current_stop

            stop_was_lowered = candidate_stop < current_stop
            ambiguous = stop_was_lowered and bar_high >= candidate_stop

            if bar_high >= current_stop:
                if ambiguous:
                    trade.ambiguous_intrabar_events += 1
                trade.exit_time = _as_utc_timestamp(bar_time)
                trade.exit_price = current_stop
                trade.exit_reason = "trail_stop" if trailing_active else "initial_stop"
                break

            trailing_active = trailing_active or (achieved_r >= cfg.trail_activation_r)
            current_stop = candidate_stop

            if ambiguous:
                trade.ambiguous_intrabar_events += 1
                trade.exit_time = _as_utc_timestamp(bar_time)
                trade.exit_price = current_stop
                trade.exit_reason = "trail_stop"
                break

    else:
        trade.exit_time = _as_utc_timestamp(exec_times[-1])
        trade.exit_price = exec_closes[-1]
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

def run_backtest(
    signal_df: pd.DataFrame,
    exec_df: pd.DataFrame,
    cfg: StrategyConfig,
    entry_start_time: Optional[pd.Timestamp] = None,
):
    """Run the strategy, optionally ignoring entries before ``entry_start_time``.

    Signal state is still generated from the whole supplied signal history.
    This is useful for an out-of-sample period: structure formed before the
    boundary remains known, while only entries inside the scored period count.
    """
    signal_df = signal_df.reset_index(drop=True)
    signal_df = detect_swings(signal_df, cfg.lookback, cfg.lookforward)
    signal_df["atr"] = compute_atr(signal_df, cfg.atr_period)

    signals = generate_signals(signal_df, cfg)

    equity = cfg.base_capital
    trades: List[Trade] = []
    open_trade_active_until = None  # simplistic: one trade at a time
    entry_start = _as_utc_timestamp(entry_start_time) if entry_start_time is not None else None

    for sig in signals:
        atr_val = signal_df["atr"].iat[sig["index"]]
        if cfg.stop_buffer_atr_mult != 0 and pd.isna(atr_val):
            continue

        trade = simulate_trade(sig, exec_df, atr_val, equity, cfg)
        if trade is None:
            continue
        if entry_start is not None and trade.entry_time < entry_start:
            continue
        if open_trade_active_until is not None and trade.entry_time < open_trade_active_until:
            continue  # only one position at a time; compare actual M1 fill times

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
    ambiguous_events = sum(t.ambiguous_intrabar_events for t in trades)
    print(f"Trades: {n}")
    print(f"Win rate: {len(wins)/n*100:.1f}%")
    print(f"Avg R multiple: {avg_r:.2f}")
    print(f"Ambiguous 1-min trailing-stop events: {ambiguous_events}")
    print(f"Final equity: {final_equity:.2f}  (from {base_capital:.2f})")
    print(f"Total return: {total_return_pct:.2f}%")
