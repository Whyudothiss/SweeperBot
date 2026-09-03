"""Local data API for the Trade Replay viewer.

Run through ``npm run dev`` in this folder.  It reads the project's existing
parquet data and exposes only the selected trade and its surrounding candles.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlparse

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategy_engine import StrategyConfig, run_backtest  # noqa: E402


class TradeStore:
    def __init__(self) -> None:
        self.signal_df = pd.read_parquet(ROOT / "data/processed/xauusd_m5_master.parquet")
        self.exec_df = pd.read_parquet(ROOT / "data/processed/xauusd_m1_master.parquet")
        self.config = StrategyConfig(
            lookback=2,
            lookforward=1,
            max_rejection_wait_bars=0,
            direction="both",
        )
        self.trades, self.final_equity = run_backtest(self.signal_df, self.exec_df, self.config)

    @staticmethod
    def _time(value: pd.Timestamp | None) -> str | None:
        return value.isoformat() if value is not None else None

    def trade_summary(self, number: int) -> dict:
        trade = self.trades[number - 1]
        return {
            "number": number,
            "entryTime": self._time(trade.entry_time),
            "exitTime": self._time(trade.exit_time),
            "direction": trade.direction,
            "entryPrice": round(trade.entry_price, 4),
            "initialStop": round(trade.initial_stop, 4),
            "exitPrice": round(trade.exit_price, 4) if trade.exit_price is not None else None,
            "exitReason": trade.exit_reason,
            "rMultiple": round(trade.r_multiple, 3) if trade.r_multiple is not None else None,
            "pnl": round(trade.pnl_dollars, 2) if trade.pnl_dollars is not None else None,
            "ambiguousEvents": trade.ambiguous_intrabar_events,
        }

    @staticmethod
    def _candles(df: pd.DataFrame, start: int, stop: int) -> list[dict]:
        result = []
        for idx, row in df.iloc[start:stop].iterrows():
            result.append(
                {
                    "index": int(idx),
                    "time": row["datetime_utc"].isoformat(),
                    "open": round(float(row["open"]), 4),
                    "high": round(float(row["high"]), 4),
                    "low": round(float(row["low"]), 4),
                    "close": round(float(row["close"]), 4),
                }
            )
        return result

    def trade_detail(self, number: int) -> dict:
        if number < 1 or number > len(self.trades):
            raise IndexError("Trade number is outside the available range.")

        trade = self.trades[number - 1]
        signal_points = [
            point
            for point in (
                trade.swept_swing_index,
                trade.sweep_extreme_index,
                trade.bos_index,
                trade.fill_index,
            )
            if point is not None
        ]
        m5_start = max(0, min(signal_points) - 30)
        m5_stop = min(len(self.signal_df), max(signal_points) + 31)

        entry_idx = int(self.exec_df["datetime_utc"].searchsorted(trade.entry_time, side="left"))
        exit_idx = int(self.exec_df["datetime_utc"].searchsorted(trade.exit_time, side="right"))
        m1_start = max(0, entry_idx - 20)
        m1_stop = min(len(self.exec_df), exit_idx + 20)
        total_m1 = m1_stop - m1_start
        max_m1 = 3000
        truncated = total_m1 > max_m1

        if truncated:
            # Preserve the entry and exit areas.  The response explains that
            # the middle has been omitted instead of silently drawing a fake
            # continuous price path.
            head_stop = m1_start + max_m1 // 2
            tail_start = m1_stop - max_m1 // 2
            m1_candles = self._candles(self.exec_df, m1_start, head_stop)
            m1_candles += self._candles(self.exec_df, tail_start, m1_stop)
        else:
            m1_candles = self._candles(self.exec_df, m1_start, m1_stop)

        swing_label = "(2,1) swing low" if trade.direction == "long" else "(2,1) swing high"
        sweep_label = "Sweep wick low" if trade.direction == "long" else "Sweep wick high"
        return {
            "trade": self.trade_summary(number),
            "m5Candles": self._candles(self.signal_df, m5_start, m5_stop),
            "m1Candles": m1_candles,
            "m1Truncated": truncated,
            "m1TotalCandles": total_m1,
            "markers": {
                "swing": {
                    "index": trade.swept_swing_index,
                    "price": round(trade.swept_level, 4),
                    "label": swing_label,
                },
                "sweep": {
                    "index": trade.sweep_extreme_index,
                    "price": round(trade.sweep_extreme, 4),
                    "label": sweep_label,
                },
                "bos": {"index": trade.bos_index, "label": "BOS close"},
                "entry": {"index": trade.fill_index, "price": round(trade.entry_price, 4), "label": "Entry"},
                "stop": {"price": round(trade.initial_stop, 4), "label": "Initial stop"},
                "exit": {
                    "time": self._time(trade.exit_time),
                    "price": round(trade.exit_price, 4) if trade.exit_price is not None else None,
                    "label": "Exit",
                },
            },
        }

    def search(self, query: dict[str, list[str]]) -> dict:
        date = query.get("date", [""])[0].strip()
        page = max(1, int(query.get("page", ["1"])[0]))
        limit = min(100, max(1, int(query.get("limit", ["30"])[0])))
        matches = [
            number
            for number, trade in enumerate(self.trades, start=1)
            if not date or trade.entry_time.strftime("%Y-%m-%d") == date
        ]
        start = (page - 1) * limit
        selected = matches[start : start + limit]
        return {
            "total": len(matches),
            "page": page,
            "limit": limit,
            "tradeNumbers": selected,
            "trades": [self.trade_summary(number) for number in selected],
        }


STORE: TradeStore | None = None
STORE_LOCK = Lock()


def get_store() -> TradeStore:
    global STORE
    with STORE_LOCK:
        if STORE is None:
            print("Loading market data and replaying the current strategy...")
            STORE = TradeStore()
            print(f"Ready: {len(STORE.trades)} trades available.")
    return STORE


class Handler(BaseHTTPRequestHandler):
    def _send(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            store = get_store()
            if parsed.path == "/api/summary":
                self._send(
                    {
                        "tradeCount": len(store.trades),
                        "lookback": store.config.lookback,
                        "lookforward": store.config.lookforward,
                        "rejectionWindow": store.config.max_rejection_wait_bars,
                    }
                )
                return
            if parsed.path == "/api/trades":
                self._send(store.search(parse_qs(parsed.query)))
                return
            if parsed.path.startswith("/api/trade/"):
                number = int(parsed.path.rsplit("/", 1)[-1])
                self._send(store.trade_detail(number))
                return
            self._send({"error": "Not found"}, 404)
        except (IndexError, ValueError) as error:
            self._send({"error": str(error)}, 400)
        except Exception as error:  # Keep the browser response useful during local use.
            self._send({"error": f"Could not load trade data: {error}"}, 500)

    def log_message(self, *_: object) -> None:
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Trade data API available at http://127.0.0.1:8765")
    server.serve_forever()
