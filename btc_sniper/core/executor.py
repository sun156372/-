# core/executor.py
import csv
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from config.config import CFG
from core.strategy import Strategy, Signal

@dataclass
class Position:
    market_slug: str; direction: str; token_id: str; entry_ask: float; shares: float
    cost: float; fee_paid: float; entry_ts: int; market_end_ts: int
    expected_ask: float; actual_ask: float; slippage: float; reason: str = ""

TRADE_FIELDS = [
    "trade_id", "mode", "tag", "market_slug", "direction", "token_id",
    "entry_ts", "entry_iso", "expected_ask", "actual_ask", "slippage",
    "shares", "cost", "fee_paid", "exit_mode", "exit_price", "exit_ts",
    "exit_iso", "realized_pnl", "result", "equity_after", "entry_remain", "reason",
]

class BaseExecutor(ABC):
    def __init__(self, strategy: Strategy, mode: str = "PAPER", tag: str = ""):
        self.strategy = strategy; self.mode = mode; self.tag = tag
        self.open_position: Optional[Position] = None; self.trade_id_seq = 0

        self.data_dir = Path(CFG.DATA_DIR); self.data_dir.mkdir(parents=True, exist_ok=True)
        self.trades_path = self.data_dir / (CFG.TRADES_FILE if not tag else f"trades_{tag}.csv")
        self.error_path = self.data_dir / CFG.ERROR_FILE
        self._init_trades_file()

    def _init_trades_file(self):
        if not self.trades_path.exists():
            with self.trades_path.open("w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=TRADE_FIELDS).writeheader()

    def _append_trade(self, row):
        row.setdefault("mode", self.mode); row.setdefault("tag", self.tag)
        with self.trades_path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=TRADE_FIELDS, extrasaction="ignore").writerow(row)

    def _log_error(self, msg):
        with self.error_path.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now(timezone.utc).isoformat()}] [{self.tag}] {msg}\n")

    def has_open_position(self) -> bool: return self.open_position is not None

    def execute(self, signal: Signal) -> bool:
        if self.has_open_position(): return False
        try: actual_price, actual_shares, fee = self._place_entry(signal)
        except Exception as e:
            self._log_error(f"开仓失败 {signal.market_slug}: {e}"); return False

        self.open_position = Position(
            market_slug=signal.market_slug, direction=signal.direction, token_id=signal.token_id,
            entry_ask=actual_price, shares=actual_shares, cost=actual_shares * actual_price + fee,
            fee_paid=fee, entry_ts=int(time.time()), market_end_ts=signal.market_end_ts,
            expected_ask=signal.entry_ask, actual_ask=actual_price,
            slippage=actual_price - signal.entry_ask, reason=signal.reason,
        )
        return True

    def on_tick(self, tick: Dict[str, Any]):
        if not self.open_position: return
        pos = self.open_position; now_ts = int(time.time()); remain = tick.get("remain_sec", 0)

        bid = self._get_exit_bid(tick, pos)
        if bid is not None and bid >= CFG.TAKE_PROFIT_BID_PRICE:
            self._close(pos, exit_price=bid, mode="TAKE_PROFIT", now_ts=now_ts, tick=tick); return
        if remain <= CFG.FORCE_CLOSE_REMAIN_SEC:
            self._close(pos, exit_price=0.0, mode="FORCE_CLOSE", now_ts=now_ts, tick=tick); return

    def _close(self, pos: Position, exit_price: float, mode: str, now_ts: int, tick: dict = None):
        if mode == "FORCE_CLOSE" and tick:
            result = self._get_settlement_result(pos, tick)
            win = (result == pos.direction)
            payout = pos.shares * 1.0 if win else 0.0
            realized = payout - pos.cost
            trade_result = "WIN" if win else "LOSS"
            exit_price = 1.0 if win else 0.0
        else:
            gross = pos.shares * exit_price
            fee_exit = Strategy.calc_taker_fee(pos.shares, exit_price) if exit_price > 0 else 0.0
            realized = gross - pos.cost - fee_exit
            trade_result = "WIN" if realized > 0 else "LOSS"

        self.strategy.update_account(realized, trade_result == "WIN", pos.market_slug)
        self.trade_id_seq += 1
        self._append_trade({
            "trade_id": self.trade_id_seq, "market_slug": pos.market_slug,
            "direction": pos.direction, "token_id": pos.token_id, "entry_ts": pos.entry_ts,
            "entry_iso": datetime.fromtimestamp(pos.entry_ts, tz=timezone.utc).isoformat(),
            "expected_ask": round(pos.expected_ask, 4), "actual_ask": round(pos.actual_ask, 4),
            "slippage": round(pos.slippage, 4), "shares": round(pos.shares, 4),
            "cost": round(pos.cost, 4), "fee_paid": round(pos.fee_paid, 4),
            "exit_mode": mode, "exit_price": round(exit_price, 4), "exit_ts": now_ts,
            "exit_iso": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
            "realized_pnl": round(realized, 4), "result": trade_result,
            "equity_after": round(self.strategy.account.equity, 4),
            "entry_remain": self.strategy.entry_remain, "reason": pos.reason,
        })
        self.open_position = None

    @abstractmethod
    def _place_entry(self, signal: Signal) -> Tuple[float, float, float]: pass
    @abstractmethod
    def _get_exit_bid(self, tick: Dict[str, Any], pos: Position) -> Optional[float]: pass
    @abstractmethod
    def _get_settlement_result(self, pos: Position, tick: Dict[str, Any]) -> str: pass


class PaperExecutor(BaseExecutor):
    def _place_entry(self, signal: Signal) -> Tuple[float, float, float]:
        actual_price = min(0.99, signal.entry_ask + CFG.PAPER_ENTRY_SLIPPAGE)
        return actual_price, signal.shares, Strategy.calc_taker_fee(signal.shares, actual_price)

    def _get_exit_bid(self, tick, pos): return tick.get("up_best_bid") if pos.direction == "UP" else tick.get("down_best_bid")

    def _get_settlement_result(self, pos, tick):
        spot, spot_open = tick.get("spot_price", 0.0), tick.get("spot_open", 0.0)
        if spot_open < 1e-9 or spot < 1e-9:
            return "UP" if (tick.get("up_best_bid") or 0.5) >= 0.5 else "DOWN"
        return "UP" if spot >= spot_open else "DOWN"


class LiveExecutor(BaseExecutor):
    """实盘执行器占位，确保 import 成功，实际交易需自行对接私钥"""
    def _place_entry(self, signal: Signal) -> Tuple[float, float, float]:
        raise NotImplementedError("实盘交易功能尚未实现，请使用 paper 模式")
    def _get_exit_bid(self, tick: Dict[str, Any], pos: Position) -> Optional[float]:
        return None
    def _get_settlement_result(self, pos: Position, tick: Dict[str, Any]) -> str:
        return "UP"
