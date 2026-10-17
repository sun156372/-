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

    # ---- 默认策略（仅作为基准，实际会被 Grid 覆盖） ----
    ENTRY_TOLERANCE_SEC: int = 3
    IMBALANCE_THRESHOLD: float = 0.30       
    TOKEN_MOVE_THRESHOLD: float = 0.20      
    SPOT_MOVE_THRESHOLD_BPS: float = 4.5    

    # ---- 网格参数列表 (参数选择器核心) ----
    # 这里的 (imb, tok, spot) 将组合成不同的测试标签
    PARAM_GRIDS: Tuple[dict, ...] = (
        {"name": "Baseline", "imb": 0.30, "tok": 0.20, "spot": 4.5}, # 原版基准
        {"name": "Loose1", "imb": 0.20, "tok": 0.10, "spot": 3.0},   # 稍微放宽，现货~25U
        {"name": "Loose2", "imb": 0.20, "tok": 0.05, "spot": 2.5},   # 中等放宽，现货~20U
        {"name": "Loose3", "imb": 0.15, "tok": 0.05, "spot": 2.0},   # 极度放宽，现货~16U
    )

    DEFAULT_ENTRY_REMAIN: int = 180

    # ---- 盘口过滤 ----
    MAX_SPREAD: float = 0.10
    MIN_ENTRY_ASK: float = 0.30       # 放宽下限，允许买入中低概率
    MAX_ENTRY_ASK: float = 0.98       # 强制 < 0.98
    MIN_BID_DEPTH_SHARES: float = 2.0 # 降低深度要求

    # ---- 手续费 ----
    FEE_THETA: float = 0.05
    TAKER_REBATE_RATE: float = 0.50

    # ---- 账户 ----
    INIT_EQUITY: float = 19.0
    ORDER_BUDGET_U: float = 18.0
    MIN_TRADE_BUDGET_U: float = 2.0

    # ---- 风控 ----
    DAILY_MAX_LOSS_U: float = 50.0    # 调高风控，防止模拟盘因为连续亏损锁死
    MAX_CONSECUTIVE_LOSSES: int = 20
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

CFG = Config()
