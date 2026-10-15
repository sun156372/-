# main.py
import argparse
import asyncio
import logging
import time
import json
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
        path = data_dir / f"rejects_{r['label']}.json"
        path.write_text(json.dumps(r["strategy"].reject_stats, ensure_ascii=False, indent=2), encoding="utf-8")

async def run_sweep(mode: str = "paper"):
    logger.info(f"启动多时间点扫描 mode={mode}")
    runners = []
    for cfg in CFG.ENTRY_TIMES:
        strat = Strategy(entry_remain=cfg["remain"], state_suffix=cfg["label"])
        exec_ = PaperExecutor(strat, tag=cfg["label"]) if mode == "paper" else LiveExecutor(strat, tag=cfg["label"])
        runners.append({"label": cfg["label"], "strategy": strat, "executor": exec_})
        logger.info(f"[Sweep] 初始化 {cfg['label']}")

    feed = Feed()
    feed_task = asyncio.create_task(feed.start())
    while not feed.token_ids:
        await asyncio.sleep(1)
        logger.info("正在等待市场发现...")

    logger.info("扫描模式启动成功，开始极速策略循环...")
    last_market_check, last_dump, last_heartbeat = 0, 0, 0
    try:
        while True:
            now_ts = int(time.time())
            if now_ts - last_market_check > 5:
                feed.discover_market()
                for r in runners: r["strategy"].update_window(feed.market_slug)
                last_market_check = now_ts
            
            tick = feed.get_tick()
            if tick:
                for r in runners:
                    r["executor"].on_tick(tick)
                    signal = r["strategy"].evaluate(tick)
                    if signal and r["executor"].execute(signal):
                        logger.info(f"[{r['label']}] 入场 {signal.direction} @{signal.entry_ask:.3f}")
                if now_ts - last_heartbeat > 5:
                    for r in runners:
                        logger.info(f"[{r['label']}] 心跳: 剩余={tick['remain_sec']}s | UpAsk={tick['up_best_ask']:.3f}")
                    last_heartbeat = now_ts
            else:
                if now_ts - last_heartbeat > 5:
                    logger.warning("未获取到 tick 数据，强制记录断流...")
                    feed.discover_market()
                    for r in runners: r["strategy"].record_feed_error("行情断流")
                    feed._rest_fetch_books(); feed._fetch_spot()
                    last_heartbeat = now_ts
                    
            if now_ts - last_dump > 10:
                dump_rejects(runners); last_dump = now_ts
            await asyncio.sleep(0.1)
    finally:
        feed.stop(); feed_task.cancel()

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p_sweep = sub.add_parser("sweep"); p_sweep.add_argument("--mode", choices=["paper", "live"], default="paper")
    p_report = sub.add_parser("report"); p_report.add_argument("--tags", nargs="*", default=["T-240", "T-210", "T-180", "T-150", "T-120"])
    args = parser.parse_args()
    if args.command == "sweep":
        asyncio.run(run_sweep(mode=args.mode))
    elif args.command == "report":
        gen = ReportGenerator()
        gen.print_compare(args.tags)
        gen.print_rejections(args.tags)

if __name__ == "__main__":
    main()
