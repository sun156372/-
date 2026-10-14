# diagnose.py
import time
import logging
from core.feed import Feed
from core.strategy import Strategy

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger("Diagnose")

def run_diagnosis():
    logger.info("启动条件诊断，每秒打印一次指标...")
    feed = Feed()
    if not feed.discover_market():
        logger.error("市场发现失败")
        return
        
    # 用默认的 T-180 来测试
    strat = Strategy(entry_remain=180)
    
    while True:
        try:
            feed._rest_fetch_books()
            feed._fetch_spot()
            tick = feed.get_tick()
            
            if tick:
                # 1. 计算各项指标
                imb = strat._calc_imbalance(tick)
                up_mv = strat._token_move(tick, "up")
                dn_mv = strat._token_move(tick, "down")
                spot_mv = strat._spot_move(tick)
                
                # 2. 打印并与阈值对比
                logger.info(
                    f"剩余={tick['remain_sec']}s | "
                    f"UpAsk={tick['up_best_ask']:.3f} | "
                    f"失衡={imb:.3f} (需≥0.30) | "
                    f"Up移动={up_mv:.3f} Dn移动={dn_mv:.3f} (需≥0.20) | "
                    f"现货={spot_mv:.1f}bps (需≥4.5)"
                )
            time.sleep(1)
        except Exception as e:
            logger.error(f"诊断异常: {e}")
            time.sleep(5)

if __name__ == "__main__":
    run_diagnosis()
