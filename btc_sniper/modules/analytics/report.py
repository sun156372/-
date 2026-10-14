# modules/analytics/report.py
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

from config.config import CFG
from modules.analytics.metrics import MetricsCalculator, Metrics

class ReportGenerator:
    def __init__(self, trades_path: Optional[Path] = None):
        self.calc = MetricsCalculator(trades_path)
        self.data_dir = Path(CFG.DATA_DIR)

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
        """打印各个时间点的信号拒绝原因分布"""
        print("\n" + "=" * 110)
        print(" 信号拒绝原因统计（模拟盘为什么没有成交）")
        print("=" * 110)
        
        for tag in tags:
            path = self.data_dir / f"rejects_{tag}.json"
            if not path.exists():
                print(f"[{tag}] 暂无拒绝记录（可能还没达到决策时间点）")
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
            # 按次数排序
            sorted_reasons = sorted(data.items(), key=lambda x: x[1], reverse=True)
            for reason, count in sorted_reasons:
                pct = count / total * 100
                # 把技术名词翻译得易读一些
                desc = {
                    "time_window_miss": "未到T-180时间点",
                    "already_traded": "本窗口已交易过",
                    "price_out_of_range": "价格不在0.86~0.93区间",
                    "spread_too_high": "盘口点差过大",
                    "depth_too_low_up": "Up盘口深度不足",
                    "depth_too_low_down": "Down盘口深度不足",
                    "imbalance_too_low": "名义价值失衡度不足",
                    "token_move_too_small": "代币移动不足",
                    "spot_move_too_small": "现货移动不足",
                    "direction_mismatch": "方向判断不一致",
                    "position_size_zero": "仓位计算为0",
                    "risk_control_max_trades": "风控：当日笔数超限",
                    "risk_control_daily_loss": "风控：当日亏损超限",
                    "risk_control_consecutive_loss": "风控：连亏超限",
                    "risk_control_low_equity": "风控：权益过低",
                    "risk_control_cooldown": "风控：冷却中",
                }.get(reason, reason)
                print(f"  {desc:<25} : {count:>6} 次 ({pct:.1f}%)")
        print("=" * 110)
