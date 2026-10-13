
# main.py
import argparse
import asyncio
import logging
import sys

from config.config import CFG
from core.feed import Feed
from core.strategy import Strategy
from core.executor import PaperExecutor, LiveExecutor
from modules.analytics.report import ReportGenerator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("Main")

async def run_single(mode: str = "paper", entry_remain: int = None):
    entry_remain = entry_remain or CFG.DEFAULT_ENTRY_REMAIN
    logger.info(f"启动单点实时循环 mode={mode} entry=T-{entry_remain}")

    feed = Feed()
    strategy = Strategy(entry_remain=entry_remain, state_suffix=f"T-{entry_remain}")
    if mode == "paper":
        executor = PaperExecutor(strategy, tag=f"T-{entry_remain}")
    else:
        executor = LiveExecutor(strategy, tag=f"T-{entry_remain}")

    # 1. 将 WebSocket 启动作为后台任务，不要卡住主线程
    feed_task = asyncio.create_task(feed.start())
    
    # 2. 等待市场发现（加超时和日志）
    wait_sec = 0
    while not feed.token_ids and wait_sec < 30:
        await asyncio.sleep(0.5)
        wait_sec += 0.5
        if int(wait_sec) % 5 == 0:
            logger.info(f"等待市场发现... {wait_sec}秒")

    if not feed.token_ids:
        logger.error("市场发现超时，请检查网络或 Gamma API。")
        feed.stop()
        return

    logger.info("市场发现成功，开始极速策略循环（100ms 响应一次）...")
    try:
        while True:
            tick = feed.get_tick()
            if tick:
                executor.on_tick(tick)
                signal = strategy.evaluate(tick)
                if signal:
                    if executor.execute(signal):
                        logger.info(f"入场 {signal.direction} @{signal.entry_ask:.3f} slug={signal.market_slug}")
            # 极速循环，100ms 检查一次，大幅降低延迟
            await asyncio.sleep(0.1)
    finally:
        feed.stop()
        feed_task.cancel()
        try:
            await feed_task
        except asyncio.CancelledError:
            pass

async def run_sweep(mode: str = "paper"):
    logger.info(f"启动多时间点扫描 mode={mode}")
    entry_times = list(CFG.ENTRY_TIMES)
    runners = []
    for cfg in entry_times:
        label = cfg["label"]
        remain = cfg["remain"]
        strat = Strategy(entry_remain=remain, state_suffix=label)
        if mode == "paper":
            exec_ = PaperExecutor(strat, tag=label)
        else:
            exec_ = LiveExecutor(strat, tag=label)
        runners.append({"label": label, "strategy": strat, "executor": exec_})
        logger.info(f"[Sweep] 初始化 {label} (remain={remain}s)")

    feed = Feed()
    feed_task = asyncio.create_task(feed.start())
    
    wait_sec = 0
    while not feed.token_ids and wait_sec < 30:
        await asyncio.sleep(0.5)
        wait_sec += 0.5
        if int(wait_sec) % 5 == 0:
            logger.info(f"等待市场发现... {wait_sec}秒")

    if not feed.token_ids:
        logger.error("市场发现超时")
        feed.stop()
        return

    logger.info("扫描模式启动成功，开始极速策略循环...")
    try:
        while True:
            tick = feed.get_tick()
            if tick:
                for r in runners:
                    r["executor"].on_tick(tick)
                    signal = r["strategy"].evaluate(tick)
                    if signal:
                        ok = r["executor"].execute(signal)
                        if ok:
                            logger.info(f"[{r['label']}] 入场 {signal.direction} @{signal.entry_ask:.3f}")
            await asyncio.sleep(0.1)
    finally:
        feed.stop()
        feed_task.cancel()
        try:
            await feed_task
        except asyncio.CancelledError:
            pass

def main():
    parser = argparse.ArgumentParser(description="Polymarket BTC 5m Sniper")
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--mode", choices=["paper", "live"], default="paper")
    p_run.add_argument("--entry", type=int, default=None)
    p_sweep = sub.add_parser("sweep")
    p_sweep.add_argument("--mode", choices=["paper", "live"], default="paper")
    p_report = sub.add_parser("report")
    p_report.add_argument("--tags", nargs="*", default=None)

    args = parser.parse_args()
    try:
        if args.command == "run":
            asyncio.run(run_single(mode=args.mode, entry_remain=args.entry))
        elif args.command == "sweep":
            asyncio.run(run_sweep(mode=args.mode))
        elif args.command == "report":
            gen = ReportGenerator()
            gen.print_compare() if not args.tags else gen.print_compare(args.tags)
    except KeyboardInterrupt:
        logger.info("已停止")

if __name__ == "__main__":
    main()
EOF
