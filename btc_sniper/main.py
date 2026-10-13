# main.py
import argparse
import asyncio
import logging
import sys

from config.config import CFG
from core.feed import Feed
from core.strategy import Strategy
from core.executor import PaperExecutor, LiveExecutor
from modules.tools.entry_time_sweep import EntryTimeSweepRunner
from modules.analytics.report import ReportGenerator


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("Main")


# ---------- 单时间点 ----------

async def run_single(mode: str = "paper", entry_remain: int = None):
    entry_remain = entry_remain or CFG.DEFAULT_ENTRY_REMAIN
    logger.info(f"启动单点实时循环 mode={mode} entry=T-{entry_remain}")

    feed = Feed()
    strategy = Strategy(entry_remain=entry_remain, state_suffix=f"T-{entry_remain}")
    if mode == "paper":
        executor = PaperExecutor(strategy, tag=f"T-{entry_remain}")
    else:
        executor = LiveExecutor(strategy, tag=f"T-{entry_remain}")

    feed_task = asyncio.create_task(feed.start())
    # 等待 feed 就绪
    while not feed.token_ids:
        await asyncio.sleep(0.5)

    try:
        while True:
            tick = feed.get_tick()
            if tick:
                executor.on_tick(tick)
                signal = strategy.evaluate(tick)
                if signal:
                    ok = executor.execute(signal)
                    if ok:
                        logger.info(f"入场 {signal.direction} @{signal.entry_ask:.3f} "
                                    f"slug={signal.market_slug}")
            await asyncio.sleep(0.3)
    finally:
        feed.stop()
        feed_task.cancel()
        try:
            await feed_task
        except asyncio.CancelledError:
            pass


# ---------- 多时间点 sweep ----------

async def run_sweep(mode: str = "paper"):
    logger.info(f"启动多时间点扫描 mode={mode}")
    entry_times = list(CFG.ENTRY_TIMES)
    runner = EntryTimeSweepRunner(entry_times, mode=mode)

    feed = Feed()
    feed_task = asyncio.create_task(feed.start())
    while not feed.token_ids:
        await asyncio.sleep(0.5)

    try:
        await runner.run(feed)
    finally:
        feed.stop()
        feed_task.cancel()
        try:
            await feed_task
        except asyncio.CancelledError:
            pass


# ---------- 报告 ----------

def run_report(tags: list = None):
    gen = ReportGenerator()
    if tags:
        gen.print_compare(tags)
    else:
        # 自动扫描 sniper_data 目录下所有 trades_*.csv 做对比
        from pathlib import Path
        data_dir = Path(CFG.DATA_DIR)
        found = []
        for f in data_dir.glob("trades_*.csv"):
            label = f.stem.replace("trades_", "")
            found.append(label)
        if found:
            gen.print_compare(sorted(found))
        else:
            gen.print_single("")


# ---------- CLI ----------

def main():
    parser = argparse.ArgumentParser(description="Polymarket BTC 5m Sniper")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="单时间点实时运行")
    p_run.add_argument("--mode", choices=["paper", "live"], default="paper")
    p_run.add_argument("--entry", type=int, default=None,
                       help="进场 remain 秒数（如 180 = T-180）")

    p_sweep = sub.add_parser("sweep", help="多时间点对比扫描")
    p_sweep.add_argument("--mode", choices=["paper", "live"], default="paper")

    p_report = sub.add_parser("report", help="打印统计报告")
    p_report.add_argument("--tags", nargs="*", default=None)

    args = parser.parse_args()

    try:
        if args.command == "run":
            asyncio.run(run_single(mode=args.mode, entry_remain=args.entry))
        elif args.command == "sweep":
            asyncio.run(run_sweep(mode=args.mode))
        elif args.command == "report":
            run_report(args.tags)
    except KeyboardInterrupt:
        logger.info("已停止")


if __name__ == "__main__":
    main()
