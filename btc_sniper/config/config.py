# config/config.py
from dataclasses import dataclass
from typing import Tuple

@dataclass
class Config:
    # ---- 市场 ----
    GAMMA_BASE_URL: str = "https://gamma-api.polymarket.com"
    CLOB_BASE_URL: str = "https://clob.polymarket.com"
    WS_URL: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    WINDOW_SECONDS: int = 300
    MARKET_SLUG_PREFIX: str = "btc-updown-5m"
    MARKET_SEARCH_OFFSETS: Tuple[int, ...] = (0, -300, 300, -600, 600)
    HTTP_RETRIES: int = 3
    HTTP_RETRY_SLEEP: float = 0.5

    # ---- WS ----
    WS_PING_INTERVAL: float = 20.0
    WS_PING_TIMEOUT: float = 10.0
    WS_RECONNECT_BASE: float = 1.0

    # ---- REST 兜底 ----
    REST_BOOK_FALLBACK_ENABLED: bool = True
    REST_BOOK_FALLBACK_INTERVAL_SEC: float = 5.0
    REST_BOOK_TIMEOUT: float = 3.0
    BOOK_STALE_SEC: float = 5.0

    # ---- 策略（恢复截图中的三大严苛参数） ----
    ENTRY_TOLERANCE_SEC: int = 3
    IMBALANCE_THRESHOLD: float = 0.30       # 参数1：失衡≥0.30
    TOKEN_MOVE_THRESHOLD: float = 0.20      # 参数2：代币移动≥0.20
    SPOT_MOVE_THRESHOLD_BPS: float = 4.5    # 参数3：现货移动≥4.5bps

    # ---- 进场时间窗口列表 ----
    ENTRY_TIMES: Tuple[dict, ...] = (
        {"label": "T-240", "remain": 240},
        {"label": "T-210", "remain": 210},
        {"label": "T-180", "remain": 180},
        {"label": "T-150", "remain": 150},
        {"label": "T-120", "remain": 120},
    )
    DEFAULT_ENTRY_REMAIN: int = 180

    # ---- 盘口过滤（新增：限制买入价格必须小于0.98） ----
    MAX_SPREAD: float = 0.08
    MIN_ENTRY_ASK: float = 0.01      # 去掉下限，由策略三条件决定
    MAX_ENTRY_ASK: float = 0.98      # 强制上限！买入价格必须 < 0.98
    MIN_BID_DEPTH_SHARES: float = 10.0

    # ---- 手续费 ----
    FEE_THETA: float = 0.05
    TAKER_REBATE_RATE: float = 0.50

    # ---- 账户 ----
    INIT_EQUITY: float = 19.0
    ORDER_BUDGET_U: float = 18.0
    MIN_TRADE_BUDGET_U: float = 2.0

    # ---- 风控 ----
    DAILY_MAX_LOSS_U: float = 15.0
    MAX_CONSECUTIVE_LOSSES: int = 5
    MAX_TRADES_PER_DAY: int = 288
    ONLY_ONE_TRADE_PER_WINDOW: bool = True
    COOLDOWN_AFTER_TRADE_SEC: int = 5

    # ---- 退出 ----
    TAKE_PROFIT_BID_PRICE: float = 0.99
    FORCE_CLOSE_REMAIN_SEC: int = 5
    PAPER_ENTRY_SLIPPAGE: float = 0.005

    # ---- 文件 ----
    DATA_DIR: str = "sniper_data"
    STATE_FILE: str = "state.json"
    TRADES_FILE: str = "trades.csv"
    ERROR_FILE: str = "errors.log"

    # ---- 实盘 ----
    LIVE_CHAIN_ID: int = 137
    LIVE_SIGNATURE_TYPE: int = 1
    LIVE_DEFAULT_TICK_SIZE: float = 0.01
    LIVE_BUY_SLIPPAGE: float = 0.03

CFG = Config()
