# modules/analytics/metrics.py
import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any, Optional

from config.config import CFG


@dataclass
class TradeRow:
    trade_id: int
    tag: str
    market_slug: str
    direction: str
    expected_ask: float
    actual_ask: float
    slippage: float
    shares: float
    cost: float
    fee_paid: float
    exit_mode: str
    exit_price: float
    realized_pnl: float
    result: str
    equity_after: float
    entry_ts: int
    exit_ts: int
    entry_remain: int


@dataclass
class Metrics:
    tag: str = ""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0

    total_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    avg_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0

    peak_equity: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    max_dd_start_ts: int = 0
    max_dd_end_ts: int = 0
    max_dd_duration_sec: int = 0

    avg_slippage: float = 0.0
    max_slippage: float = 0.0
    min_slippage: float = 0.0
    total_slippage_cost: float = 0.0

    total_fee_paid: float = 0.0

    equity_start: float = 0.0
    equity_end: float = 0.0
    return_pct: float = 0.0
    sharpe: float = 0.0
    max_win_streak: int = 0
    max_loss_streak: int = 0

    tp_count: int = 0
    force_count: int = 0


class MetricsCalculator:
    def __init__(self, trades_path: Optional[Path] = None):
        self.trades_path = trades_path or (Path(CFG.DATA_DIR) / CFG.TRADES_FILE)

    def load_trades(self) -> List[TradeRow]:
        rows: List[TradeRow] = []
        if not self.trades_path.exists():
            return rows
        with self.trades_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                try:
                    rows.append(TradeRow(
                        trade_id=int(r["trade_id"]),
                        tag=r.get("tag", ""),
                        market_slug=r["market_slug"],
                        direction=r["direction"],
                        expected_ask=float(r["expected_ask"]),
                        actual_ask=float(r["actual_ask"]),
                        slippage=float(r["slippage"]),
                        shares=float(r["shares"]),
                        cost=float(r["cost"]),
                        fee_paid=float(r["fee_paid"]),
                        exit_mode=r["exit_mode"],
                        exit_price=float(r["exit_price"]),
                        realized_pnl=float(r["realized_pnl"]),
                        result=r["result"],
                        equity_after=float(r["equity_after"]),
                        entry_ts=int(r["entry_ts"]),
                        exit_ts=int(r["exit_ts"]),
                        entry_remain=int(r.get("entry_remain", 0)),
                    ))
                except Exception:
                    continue
        return rows

    def compute(self, trades: Optional[List[TradeRow]] = None, tag: str = "") -> Metrics:
        if trades is None:
            trades = self.load_trades()
            if tag:
                trades = [t for t in trades if t.tag == tag]

        m = Metrics(tag=tag)
        if not trades:
            return m

        m.total_trades = len(trades)
        m.equity_start = CFG.INIT_EQUITY
        m.equity_end = trades[-1].equity_after

        m.wins = sum(1 for t in trades if t.result == "WIN")
        m.losses = m.total_trades - m.wins
        m.win_rate = m.wins / m.total_trades

        m.total_pnl = sum(t.realized_pnl for t in trades)
        m.gross_profit = sum(t.realized_pnl for t in trades if t.realized_pnl > 0)
        m.gross_loss = -sum(t.realized_pnl for t in trades if t.realized_pnl < 0)
        m.avg_pnl = m.total_pnl / m.total_trades

        win_pnls = [t.realized_pnl for t in trades if t.result == "WIN"]
        loss_pnls = [t.realized_pnl for t in trades if t.result == "LOSS"]
        m.avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0.0
        m.avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0.0
        m.profit_factor = m.gross_profit / m.gross_loss if m.gross_loss > 1e-9 else float("inf")
        m.expectancy = m.win_rate * m.avg_win + (1 - m.win_rate) * m.avg_loss

        self._compute_drawdown(trades, m)
        self._compute_slippage(trades, m)

        m.total_fee_paid = sum(t.fee_paid for t in trades)
        if m.equity_start > 1e-9:
            m.return_pct = (m.equity_end - m.equity_start) / m.equity_start

        self._compute_streaks(trades, m)
        self._compute_sharpe(trades, m)

        m.tp_count = sum(1 for t in trades if t.exit_mode == "TAKE_PROFIT")
        m.force_count = sum(1 for t in trades if t.exit_mode == "FORCE_CLOSE")
        return m

    def _compute_drawdown(self, trades: List[TradeRow], m: Metrics):
        equity = CFG.INIT_EQUITY
        peak = equity
        max_dd = 0.0
        max_dd_start = 0
        max_dd_end = 0
        cur_dd_start = None
        for t in trades:
            equity = t.equity_after
            if equity > peak:
                peak = equity
                cur_dd_start = None
            dd = peak - equity
            if dd > 1e-9 and cur_dd_start is None:
                cur_dd_start = t.exit_ts
            if dd > max_dd:
                max_dd = dd
                max_dd_start = cur_dd_start or t.entry_ts
                max_dd_end = t.exit_ts
        m.peak_equity = peak
        m.max_drawdown = max_dd
        m.max_drawdown_pct = max_dd / peak if peak > 1e-9 else 0.0
        m.max_dd_start_ts = max_dd_start
        m.max_dd_end_ts = max_dd_end
        m.max_dd_duration_sec = max(0, max_dd_end - max_dd_start)

    def _compute_slippage(self, trades, m):
        slips = [t.slippage for t in trades]
        if not slips:
            return
        m.avg_slippage = sum(slips) / len(slips)
        m.max_slippage = max(slips)
        m.min_slippage = min(slips)
        m.total_slippage_cost = sum(t.slippage * t.shares for t in trades)

    def _compute_streaks(self, trades, m):
        cur_win = cur_loss = 0
        for t in trades:
            if t.result == "WIN":
                cur_win += 1; cur_loss = 0
                m.max_win_streak = max(m.max_win_streak, cur_win)
            else:
                cur_loss += 1; cur_win = 0
                m.max_loss_streak = max(m.max_loss_streak, cur_loss)

    def _compute_sharpe(self, trades, m):
        returns = [t.realized_pnl / t.cost for t in trades if t.cost > 1e-9]
        if len(returns) < 2:
            return
        mean_r = sum(returns) / len(returns)
        var = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
        std = var ** 0.5
        if std > 1e-9:
            m.sharpe = mean_r / std
