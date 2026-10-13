# modules/analytics/report.py
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

from config.config import CFG
from modules.analytics.metrics import MetricsCalculator, Metrics


class ReportGenerator:
    def __init__(self, trades_path: Optional[Path] = None):
        self.calc = MetricsCalculator(trades_path)

    def print_single(self, tag: str = ""):
        m = self.calc.compute(tag=tag)
        self._print_header(m)
        self._print_body(m)

    def print_compare(self, tags: List[str]):
        rows = []
        for tag in tags:
            m = self.calc.compute(tag=tag)
            rows.append(m)

        print("=" * 110)
        print(" 进场时间对比（多时间点扫描）")
        print("=" * 110)
        headers = ["Tag", "Trades", "Win%", "PnL", "Return%", "PF", "Exp",
                   "MaxDD%", "Sharpe", "MaxLossStreak", "AvgSlip", "TP/Force"]
        widths = [8, 7, 7, 9, 9, 6, 8, 8, 8, 14, 8, 10]
        print("".join(h.ljust(w) for h, w in zip(headers, widths)))
        print("-" * 110)
        for m in rows:
            cells = [
                m.tag,
                str(m.total_trades),
                f"{m.win_rate:.1%}",
                f"{m.total_pnl:+.3f}",
                f"{m.return_pct:+.1%}",
                f"{m.profit_factor:.2f}",
                f"{m.expectancy:+.3f}",
                f"{m.max_drawdown_pct:.1%}",
                f"{m.sharpe:.2f}",
                str(m.max_loss_streak),
                f"{m.avg_slippage:+.4f}",
                f"{m.tp_count}/{m.force_count}",
            ]
            print("".join(c.ljust(w) for c, w in zip(cells, widths)))
        print("=" * 110)

    # ---------- 内部 ----------

    def _print_header(self, m: Metrics):
        print("=" * 60)
        print(f" Polymanrket BTC 5m Sniper 报告  tag={m.tag or 'DEFAULT'}")
        print("=" * 60)

    def _print_body(self, m: Metrics):
        if m.total_trades == 0:
            print(" 暂无交易记录")
            print("=" * 60)
            return
        print(f" 交易总数        : {m.total_trades}  (TP={m.tp_count} / Force={m.force_count})")
        print(f" 盈利 / 亏损     : {m.wins} / {m.losses}")
        print(f" 胜率            : {m.win_rate:.2%}")
        print()
        print(f" 总盈亏          : {m.total_pnl:+.4f} U")
        print(f" 结束权益        : {m.equity_end:.4f} U  (起始 {m.equity_start:.4f})")
        print(f" 收益率          : {m.return_pct:+.2%}")
        print()
        print(f" 平均单笔        : {m.avg_pnl:+.4f} U")
        print(f" 平均盈利        : {m.avg_win:+.4f} U")
        print(f" 平均亏损        : {m.avg_loss:+.4f} U")
        print(f" 单笔期望        : {m.expectancy:+.4f} U")
        print(f" 盈亏比(PF)      : {m.profit_factor:.3f}")
        print()
        print(f" 峰值权益        : {m.peak_equity:.4f} U")
        print(f" 最大回撤        : {m.max_drawdown:.4f} U ({m.max_drawdown_pct:.2%})")
        print(f" 回撤持续        : {self._fmt_duration(m.max_dd_duration_sec)}")
        print()
        print(f" 平均滑点        : {m.avg_slippage:+.4f}")
        print(f" 最大滑点        : {m.max_slippage:+.4f}")
        print(f" 滑点成本累计    : {m.total_slippage_cost:+.4f} U")
        print(f" 手续费累计      : {m.total_fee_paid:.4f} U")
        print(f" 最大连赢        : {m.max_win_streak}")
        print(f" 最大连亏        : {m.max_loss_streak}")
        print(f" 单笔夏普        : {m.sharpe:.3f}")
        print("=" * 60)

    @staticmethod
    def _fmt_duration(sec: int) -> str:
        if sec < 60:
            return f"{sec}s"
        if sec < 3600:
            return f"{sec // 60}m {sec % 60}s"
        return f"{sec // 3600}h {(sec % 3600) // 60}m"
