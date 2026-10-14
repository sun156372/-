# test_trade.py
import time
from core.strategy import Strategy, Signal
from core.executor import PaperExecutor

def test_paper_execution():
    print("开始测试纸面下单逻辑...")
    
    # 1. 初始化策略和纸面执行器
    strategy = Strategy(entry_remain=180, state_suffix="TEST")
    executor = PaperExecutor(strategy, tag="TEST")
    
    # 2. 强制构造一个虚拟信号（模拟市场满足条件时的状态）
    # 假设我们在 T-180 秒，以 0.88 的价格买入 10 股
    dummy_signal = Signal(
        direction="UP",
        token_id="dummy_token_123",
        entry_ask=0.88,
        shares=10.0,
        cost=10.0 * 0.88 + 0.05, # 加上预估手续费
        fee_est=0.05,
        market_slug="btc-updown-5m-测试",
        up_token_id="dummy_up",
        down_token_id="dummy_down",
        market_end_ts=int(time.time()) + 180, # 窗口剩余 180 秒
        reason="手动测试信号"
    )
    
    # 3. 执行模拟下单
    print(f"注入信号: {dummy_signal.direction} @ {dummy_signal.entry_ask}")
    ok = executor.execute(dummy_signal)
    
    if ok:
        print("✅ 模拟下单成功！")
        print(f"当前持仓: {executor.open_position}")
        
        # 4. 模拟 0.99 止盈平仓
        print("模拟止盈平仓...")
        dummy_tick = {
            "up_best_bid": 0.99,
            "down_best_bid": 0.10,
            "remain_sec": 150
        }
        executor.on_tick(dummy_tick)
        print("✅ 平仓完成！")
        print(f"账户状态: {strategy.account}")
    else:
        print("❌ 模拟下单失败！")

if __name__ == "__main__":
    test_paper_execution()
