# diagnose.py
import time
import logging
from core.feed import Feed
from core.strategy import Strategy

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger("Diagnose")

def run_diagnosis():
    logger.info("启动条件诊断...")
    feed = Feed()
    
    # 尝试发现市场
    for i in range(5):
        if feed.discover_market():
            logger.info(f"市场发现成功! Slug: {feed.market_slug}, Tokens: {feed.token_ids}")
            break
        logger.info(f"市场发现失败，重试中 ({i+1}/5)...")
        time.sleep(2)
    else:
        logger.error("连续5次市场发现失败，请检查网络或 Gamma API。")
        return

    logger.info("开始循环获取盘口数据...")
    strat = Strategy(entry_remain=180)
    wait_count = 0
    
    while True:
        try:
            # 手动触发 REST 抓取
            feed._rest_fetch_books()
            feed._fetch_spot()
            
            tick = feed.get_tick()
            if tick:
                wait_count = 0
                imb = strat._calc_imbalance(tick)
                up_mv = strat._token_move(tick, "up")
                dn_mv = strat._token_move(tick, "down")
                spot_mv = strat._spot_move(tick)
                
                logger.info(
                    f"剩余={tick['remain_sec']}s | "
                    f"UpAsk={tick['up_best_ask']:.3f} | "
                    f"失衡={imb:.3f} (需≥0.30) | "
                    f"Up移动={up_mv:.3f} Dn移动={dn_mv:.3f} (需≥0.20) | "
                    f"现货={spot_mv:.1f}bps (需≥4.5)"
                )
            else:
                wait_count += 1
                if wait_count % 5 == 1:
                    logger.warning(f"Tick 数据为空 (已等待 {wait_count} 秒)。当前 books数量: {len(feed.books)}, 现货: {feed.spot_price}")
                    # 尝试重新触发发现市场
                    if wait_count % 15 == 0:
                        logger.info("尝试重新发现市场...")
                        feed.discover_market()
            time.sleep(1)
        except Exception as e:
            logger.error(f"诊断异常: {e}")
            time.sleep(5)

if __name__ == "__main__":
    run_diagnosis()
