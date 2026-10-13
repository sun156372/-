# modules/tools/entry_time_sweep.py
import asyncio
from typing import List, Dict, Any

from config.config import CFG
from core.strategy import Strategy
from core.executor import PaperExecutor, LiveExecutor


class EntryTimeSweepRunner:
    """
    同时驱动多个进场时点，每个时点一个独立 Strategy + Executor。
    共享同一个 Feed，最终各自落盘到 trades_<label>.csv。
    """

    def __init__(self, entry_times: List[Dict[str, Any]], mode: str = "paper"):
        self.mode = mode
        self.runners = []
        for cfg in entry_times:
            label = cfg["label"]
            remain = cfg["remain"]
            strat = Strategy(entry_remain=remain, state_suffix=label)
            if mode == "paper":
                exec_ = PaperExecutor(strat, tag=label)
            else:
                exec_ = LiveExecutor(strat, tag=label)
            self.runners.append({
                "label": label,
                "remain": remain,
                "strategy": strat,
                "executor": exec_,
            })
            print(f"[Sweep] 初始化 {label} (remain={remain}s)")

    async def run(self, feed):
        """feed 需要已经 async start 的协程，这里直接消费 get_tick"""
        print(f"[Sweep] 开始运行，共 {len(self.runners)} 个时间点")
        last_report_ts = 0
        try:
            while True:
                tick = feed.get_tick()
                if tick:
                    for r in self.runners:
                        # 先处理已有持仓
                        r["executor"].on_tick(tick)
                        # 再评估新信号
                        signal = r["strategy"].evaluate(tick)
                        if signal:
                            ok = r["executor"].execute(signal)
                            if ok:
                                print(f"[{r['label']}] 入场 {signal.direction} "
                                      f"@{signal.entry_ask:.3f} slug={signal.market_slug}")
                await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            print("[Sweep] 被取消，退出")
            raise
