# modules/analytics/report.py
import json
from pathlib import Path
from typing import Optional, List
from config.config import CFG
from modules.analytics.metrics import MetricsCalculator

class ReportGenerator:
    def __init__(self, trades_path: Optional[Path] = None):
        self.calc = MetricsCalculator(trades_path)
        self.data_dir = Path(CFG.DATA_DIR)

    def print_compare(self, tags: List[str]):
        rows = [self.calc.compute(tag=tag) for tag in tags]
        print("=" * 110)
        print(" 进场时间对比（多时间点扫描）")
        print("=" * 110)
        headers = ["Tag", "Trades", "Win%", "PnL", "Return%", "PF", "Exp", "MaxDD%", "Sharpe", "MaxLossStreak", "AvgSlip", "TP/Force"]
        widths = [8, 7, 7, 9, 9, 6, 8, 8, 8, 14, 8, 10]
        print("".join(h.ljust(w) for h, w in zip(headers, widths)))
        print("-" * 110)
        for m in rows:
            cells = [
                m.tag, str(m.total_trades), f"{m.win_rate:.1%}", f"{m.total_pnl:+.3f}",
                f"{m.return_pct:+.1%}", f"{m.profit_factor:.2f}", f"{m.expectancy:+.3f}",
                f"{m.max_drawdown_pct:.1%}", f"{m.sharpe:.2f}", str(m.max_loss_streak),
                f"{m.avg_slippage:+.4f}", f"{m.tp_count}/{m.force_count}",
            ]
            print("".join(c.ljust(w) for c, w in zip(cells, widths)))
        print("=" * 110)

    def print_rejections(self, tags: List[str]):
        print("\n" + "=" * 110)
        print(" 信号拒绝原因统计（模拟盘为什么没有成交）")
        print("=" * 110)
        for tag in tags:
            path = self.data_dir / f"rejects_{tag}.json"
            if not path.exists():
                print(f"[{tag}] 暂无拒绝记录")
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            total = sum(data.values())
            if total == 0:
                print(f"[{tag}] 暂无拒绝记录")
                continue
            print(f"\n[{tag}] 总评估次数: {total}")
            for reason, count in sorted(data.items(), key=lambda x: x[1], reverse=True):
                print(f"  {reason:<25} : {count:>6} 次 ({count / total * 100:.1f}%)")
        print("=" * 110)
