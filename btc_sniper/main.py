# main.py
import argparse
import asyncio
import logging
import time
import json
import sys
from pathlib import Path

from config.config import CFG
from core.feed import Feed
from core.strategy import Strategy
from core.executor import PaperExecutor, LiveExecutor
from modules.analytics.report import ReportGenerator

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("Main")

def dump_rejects(runners):
    data_dir = Path(CFG.DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)
    for r in runners:
        if r["strategy"].reject_stats:
            path = data_dir / f"rejects_{r['label']}.json"
            path.write_text(json.dumps(r["strategy"].reject_stats, ensure_ascii=False, indent=2), encoding="utf-8")

async def run_single(mode: str = "paper", entry_remain: int = None):
    entry_remain = entry_remain or CFG.DEFAULT_ENTRY_REMAIN
    logger.info(f"启动单点实时循环 mode={mode} entry=T-{entry_remain}")
    feed = Feed()
    strategy = Strategy(entry_remain=entry_remain, state_suffix=f"T-{entry_remain}")
    executor = PaperExecutor(strategy, tag=f"T-{entry_remain}") if mode == "paper" else LiveExecutor(strategy, tag=f"T-{entry_remain}")
    feed_task = asyncio.create_task(feed.start())
    
    wait_sec = 0
    while not feed.token_ids and wait_sec < 30:
        await asyncio.sleep(0.5); wait_sec += 0.5
    if not feed.token_ids:
        logger.error("市场发现超时"); feed.stop(); return

    logger.info("市场发现成功，开始极速策略循环...")
    last_market_check = 0
    last_dump = 0
    try:
        while True:
            now_ts = int(time.time())
            if now_ts - last_market_check > 5:
                feed.discover_market(); last_market_check = now_ts
            tick = feed.get_tick()
            if tick:
                executor.on_tick(tick)
                signal = strategy.evaluate(tick)
                if signal:
                    if executor.execute(signal):
                        logger.info(f"入场 {signal.direction} @{signal.entry_ask:.3f} slug={signal.market_slug}")
            
            if now_ts - last_dump > 10:  # 每10秒落盘一次统计
                dump_rejects([{"label": f"T-{entry_remain}", "strategy": strategy}])
                last_dump = now_ts
            await asyncio.sleep(0.1)
    finally:
        feed.stop()
        feed_task.cancel()

async def run_sweep(mode: str = "paper"):
    logger.info(f"启动多时间点扫描 mode={mode}")
    runners = []
    for cfg in CFG.ENTRY_TIMES:
        label = cfg["label"]; remain = cfg["remain"]
        strat = Strategy(entry_remain=remain, state_suffix=label)
        exec_ = PaperExecutor(strat, tag=label) if mode == "paper" else LiveExecutor(strat, tag=label)
        runners.append({"label": label, "strategy": strat, "executor": exec_})
        logger.info(f"[Sweep] 初始化 {label} (remain={remain}s)")

    feed = Feed()
    feed_task = asyncio.create_task(feed.start())
    wait_sec = 0
    while not feed.token_ids and wait_sec < 30:
        await asyncio.sleep(0.5); wait_sec += 0.5
    if not feed.token_ids:
        logger.error("市场发现超时"); feed.stop(); return

    logger.info("扫描模式启动成功，开始极速策略循环...")
    last_market_check = 0
    last_dump = 0
    try:
        while True:
            now_ts = int(time.time())
            if now_ts - last_market_check > 5:
                feed.discover_market(); last_market_check = now_ts
            tick = feed.get_tick()
            if tick:
                for r in runners:
                    r["executor"].on_tick(tick)
                    signal = r["strategy"].evaluate(tick)
                    if signal:
                        if r["executor"].execute(signal):
                            logger.info(f"[{r['label']}] 入场 {signal.direction} @{signal.entry_ask:.3f}")
            
            if now_ts - last_dump > 10:
                dump_rejects(runners)
                last_dump = now_ts
            await asyncio.sleep(0.1)
    finally:
        feed.stop()
        feed_task.cancel()

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
            gen.print_compare(args.tags or ["T-240", "T-210", "T-180", "T-150", "T-120"])
            gen.print_rejections(args.tags or ["T-240", "T-210", "T-180", "T-150", "T-120"]) # 增加拒绝打印
    except KeyboardInterrupt:
        logger.info("已停止")
        # 退出前保存一次
        pass

if __name__ == "__main__":
    main()
