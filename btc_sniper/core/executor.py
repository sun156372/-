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
    market_slug: str
    direction: str
    token_id: str
    entry_ask: float
    shares: float
    cost: float
    fee_paid: float
    entry_ts: int
    market_end_ts: int
    expected_ask: float
    actual_ask: float
    slippage: float
    reason: str = ""


TRADE_FIELDS = [
    "trade_id", "mode", "tag", "market_slug", "direction", "token_id",
    "entry_ts", "entry_iso", "expected_ask", "actual_ask", "slippage",
    "shares", "cost", "fee_paid",
    "exit_mode", "exit_price", "exit_ts", "exit_iso",
    "realized_pnl", "result",
    "equity_after", "entry_remain", "reason",
]


class BaseExecutor(ABC):
    """
    执行器基类：止盈平仓 + 窗口末强制平仓。
    """

    def __init__(self, strategy: Strategy, mode: str = "PAPER", tag: str = ""):
        self.strategy = strategy
        self.mode = mode
        self.tag = tag
        self.open_position: Optional[Position] = None
        self.trade_id_seq = 0

        self.data_dir = Path(CFG.DATA_DIR)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        fname = CFG.TRADES_FILE if not tag else f"trades_{tag}.csv"
        self.trades_path = self.data_dir / fname
        self.error_path = self.data_dir / CFG.ERROR_FILE
        self._init_trades_file()

    def _init_trades_file(self):
        if not self.trades_path.exists():
            with self.trades_path.open("w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=TRADE_FIELDS).writeheader()

    def _append_trade(self, row: Dict[str, Any]):
        row.setdefault("mode", self.mode)
        row.setdefault("tag", self.tag)
        with self.trades_path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=TRADE_FIELDS, extrasaction="ignore")
            w.writerow(row)

    def _log_error(self, msg: str):
        with self.error_path.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now(timezone.utc).isoformat()}] [{self.tag}] {msg}\n")

    # ---------- 公开接口 ----------

    def has_open_position(self) -> bool:
        return self.open_position is not None

    def execute(self, signal: Signal) -> bool:
        if self.has_open_position():
            return False
        try:
            actual_price, actual_shares, fee = self._place_entry(signal)
        except Exception as e:
            self._log_error(f"开仓失败 {signal.market_slug}: {e}")
            return False

        now_ts = int(time.time())
        self.open_position = Position(
            market_slug=signal.market_slug,
            direction=signal.direction,
            token_id=signal.token_id,
            entry_ask=actual_price,
            shares=actual_shares,
            cost=actual_shares * actual_price + fee,
            fee_paid=fee,
            entry_ts=now_ts,
            market_end_ts=signal.market_end_ts,
            expected_ask=signal.entry_ask,
            actual_ask=actual_price,
            slippage=actual_price - signal.entry_ask,
            reason=signal.reason,
        )
        return True

    def on_tick(self, tick: Dict[str, Any]):
        if not self.open_position:
            return
        pos = self.open_position
        now_ts = int(time.time())
        remain = tick.get("remain_sec", 0)

        # 1. 止盈：bid 达到目标
        bid = self._get_exit_bid(tick, pos)
        if bid is not None and bid >= CFG.TAKE_PROFIT_BID_PRICE:
            self._close(pos, exit_price=bid, mode="TAKE_PROFIT", now_ts=now_ts)
            return

        # 2. 窗口结束前强制平仓
        if remain <= CFG.FORCE_CLOSE_REMAIN_SEC:
            force_price = bid if bid is not None else 0.0
            self._close(pos, exit_price=force_price, mode="FORCE_CLOSE", now_ts=now_ts)
            return

    # ---------- 平仓 ----------

    def _close(self, pos: Position, exit_price: float, mode: str, now_ts: int):
        gross = pos.shares * exit_price
        fee_exit = Strategy.calc_taker_fee(pos.shares, exit_price) if exit_price > 0 else 0.0
        realized = gross - pos.cost - fee_exit
        result = "WIN" if realized > 0 else "LOSS"

        # 更新账户
        self.strategy.update_account(realized, result == "WIN", pos.market_slug)

        # 写日志
        self.trade_id_seq += 1
        row = {
            "trade_id": self.trade_id_seq,
            "market_slug": pos.market_slug,
            "direction": pos.direction,
            "token_id": pos.token_id,
            "entry_ts": pos.entry_ts,
            "entry_iso": datetime.fromtimestamp(pos.entry_ts, tz=timezone.utc).isoformat(),
            "expected_ask": round(pos.expected_ask, 4),
            "actual_ask": round(pos.actual_ask, 4),
            "slippage": round(pos.slippage, 4),
            "shares": round(pos.shares, 4),
            "cost": round(pos.cost, 4),
            "fee_paid": round(pos.fee_paid, 4),
            "exit_mode": mode,
            "exit_price": round(exit_price, 4),
            "exit_ts": now_ts,
            "exit_iso": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
            "realized_pnl": round(realized, 4),
            "result": result,
            "equity_after": round(self.strategy.account.equity, 4),
            "entry_remain": self.strategy.entry_remain,
            "reason": pos.reason,
        }
        self._append_trade(row)
        self.open_position = None

    # ---------- 子类实现 ----------

    @abstractmethod
    def _place_entry(self, signal: Signal) -> Tuple[float, float, float]:
        """返回 (成交价, 股数, 手续费)"""

    @abstractmethod
    def _get_exit_bid(self, tick: Dict[str, Any], pos: Position) -> Optional[float]:
        """返回当前可卖的 best bid"""


class PaperExecutor(BaseExecutor):
    """纸面执行器：模拟吃单滑点，用 tick 里的 best bid 模拟平仓。"""

    def _place_entry(self, signal: Signal) -> Tuple[float, float, float]:
        actual_price = min(0.99, signal.entry_ask + CFG.PAPER_ENTRY_SLIPPAGE)
        shares = signal.shares
        fee = Strategy.calc_taker_fee(shares, actual_price)
        return actual_price, shares, fee

    def _get_exit_bid(self, tick: Dict[str, Any], pos: Position) -> Optional[float]:
        return tick.get("up_best_bid") if pos.direction == "UP" else tick.get("down_best_bid")


class LiveExecutor(BaseExecutor):
    """实盘执行器：py-clob-client-v2 市价单。"""

    CONFIRM_FILE = "live_trading_confirm.txt"
    CONFIRM_TEXT = "ENABLE_LIVE_TRADING"

    def __init__(self, strategy: Strategy, tag: str = ""):
        super().__init__(strategy, mode="LIVE", tag=tag)
        self._init_client()

    def _init_client(self):
        confirm_path = Path(self.CONFIRM_FILE)
        if not confirm_path.exists() or confirm_path.read_text().strip() != self.CONFIRM_TEXT:
            raise RuntimeError(
                f"实盘被阻止。请创建 {self.CONFIRM_FILE} 并写入: {self.CONFIRM_TEXT}"
            )
        import os
        try:
            from py_clob_client_v2 import (
                ApiCreds, ClobClient, OrderType, Side,
                MarketOrderArgs, PartialCreateOrderOptions,
            )
        except ImportError as e:
            raise RuntimeError(f"缺少 py-clob-client-v2: {e}")

        pk = os.environ.get("POLY_PRIVATE_KEY")
        funder = os.environ.get("POLY_FUNDER")
        if not pk or not funder:
            raise RuntimeError("缺少 POLY_PRIVATE_KEY 或 POLY_FUNDER")

        creds = ApiCreds(
            api_key=os.environ.get("CLOB_API_KEY", ""),
            api_secret=os.environ.get("CLOB_SECRET", ""),
            api_passphrase=os.environ.get("CLOB_PASS_PHRASE", ""),
        )
        self._client = ClobClient(
            host=CFG.CLOB_BASE_URL,
            chain_id=CFG.LIVE_CHAIN_ID,
            key=pk, creds=creds,
            signature_type=CFG.LIVE_SIGNATURE_TYPE,
            funder=funder,
        )
        self._side_enum = Side
        self._order_type_enum = OrderType
        self._MarketOrderArgs = MarketOrderArgs
        self._PartialCreateOrderOptions = PartialCreateOrderOptions

    def _place_entry(self, signal: Signal) -> Tuple[float, float, float]:
        args = self._MarketOrderArgs(
            token_id=signal.token_id,
            amount=signal.cost,
            side=self._side_enum.BUY,
        )
        options = self._PartialCreateOrderOptions(tick_size=CFG.LIVE_DEFAULT_TICK_SIZE)
        resp = self._client.create_and_post_market_order(
            order_args=args, options=options, order_type=self._order_type_enum.FAK,
        )
        if not resp or resp.get("status") not in ("matched", "filled"):
            raise RuntimeError(f"实盘未成交: {resp}")
        actual_price = float(resp.get("avg_price") or signal.entry_ask)
        actual_shares = float(resp.get("filled_size") or signal.shares)
        fee = Strategy.calc_taker_fee(actual_shares, actual_price)
        return actual_price, actual_shares, fee

    def _get_exit_bid(self, tick: Dict[str, Any], pos: Position) -> Optional[float]:
        return tick.get("up_best_bid") if pos.direction == "UP" else tick.get("down_best_bid")
