# core/strategy.py
import time
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from config.config import CFG

@dataclass
class Signal:
    direction: str
    token_id: str
    entry_ask: float
    shares: float
    cost: float
    fee_est: float
    market_slug: str
    up_token_id: str
    down_token_id: str
    market_end_ts: int
    reason: str = ""

@dataclass
class AccountState:
    equity: float
    peak_equity: float
    day: str
    daily_pnl: float = 0.0
    trades_today: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    consecutive_losses: int = 0
    total_pnl: float = 0.0

    @staticmethod
    def fresh() -> "AccountState":
        from datetime import date
        return AccountState(equity=CFG.INIT_EQUITY, peak_equity=CFG.INIT_EQUITY, day=date.today().isoformat())

    def reset_daily(self):
        from datetime import date
        today = date.today().isoformat()
        if self.day != today:
            self.day = today
            self.daily_pnl = 0.0
            self.trades_today = 0
            self.consecutive_losses = 0

class Strategy:
    def __init__(self, entry_remain: int = None, state_suffix: str = ""):
        self.entry_remain = entry_remain if entry_remain is not None else CFG.DEFAULT_ENTRY_REMAIN
        self.state_suffix = state_suffix
        self.account = AccountState.fresh()
        self.traded_windows: set = set()
        self.last_trade_ts: float = 0.0
        
        # 窗口级拒绝记录
        self.current_window_slug: str = ""
        self.last_reject_reason: str = ""  
        self.reject_stats: Dict[str, int] = {} 
        
        self._load_state()

    @property
    def state_path(self) -> Path:
        name = f"state_{self.state_suffix}.json" if self.state_suffix else CFG.STATE_FILE
        return Path(CFG.DATA_DIR) / name

    def _load_state(self):
        path = self.state_path
        if not path.exists(): return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            acc_data = data.get("account", {})
            if acc_data: self.account = AccountState(**acc_data)
            self.traded_windows = set(data.get("traded_windows", []))
            self.reject_stats = data.get("reject_stats", {}) 
            self.account.reset_daily()
        except Exception: pass

    def _save_state(self):
        path = self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "account": asdict(self.account), 
            "traded_windows": list(self.traded_windows)[-500:],
            "reject_stats": self.reject_stats
        }
        tmp = path.with_name(path.stem + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        for _ in range(10):
            try:
                tmp.replace(path); return
            except PermissionError: time.sleep(0.1)
        tmp.replace(path)

    @staticmethod
    def calc_taker_fee(shares: float, price: float) -> float:
        return max(0.0, CFG.FEE_THETA * shares * price * (1.0 - price) * (1.0 - CFG.TAKER_REBATE_RATE))

    def _check_risk(self, now_ts: int) -> Tuple[bool, str]:
        acc = self.account
        if acc.trades_today >= CFG.MAX_TRADES_PER_DAY: return False, "风控:当日笔数超限"
        if acc.daily_pnl <= -CFG.DAILY_MAX_LOSS_U: return False, "风控:当日亏损超限"
        if acc.consecutive_losses >= CFG.MAX_CONSECUTIVE_LOSSES: return False, "风控:连亏超限"
        if acc.equity < CFG.INIT_EQUITY * 0.05: return False, "风控:权益过低"
        if now_ts - self.last_trade_ts < CFG.COOLDOWN_AFTER_TRADE_SEC: return False, "风控:冷却中"
        return True, "OK"

    def _record_reject(self, reason: str):
        # 只要还在当前窗口，就只记录第一个失败原因
        if self.current_window_slug in self.traded_windows:
            return
        if not self.last_reject_reason:
            self.last_reject_reason = reason

    def evaluate(self, tick: Dict[str, Any]) -> Optional[Signal]:
        remain = tick["remain_sec"]
        now_ts = tick["ts"]
        slug = tick["market_slug"]

        # === 核心修复：窗口切换时，归档上一窗口的拒绝原因 ===
        if slug != self.current_window_slug:
            # 如果上一窗口有失败原因，记录一次
            if self.last_reject_reason:
                self.reject_stats[self.last_reject_reason] = self.reject_stats.get(self.last_reject_reason, 0) + 1
            self.current_window_slug = slug
            self.last_reject_reason = ""

        # === 核心修复：不再记录“未到时间点”的拒绝 ===
        if remain > self.entry_remain:
            return None  # 直接返回，不记录任何原因
        if remain <= CFG.FORCE_CLOSE_REMAIN_SEC:
            return None  # 直接返回，不记录任何原因

        if CFG.ONLY_ONE_TRADE_PER_WINDOW and slug in self.traded_windows:
            return None

        allowed, reason = self._check_risk(now_ts)
        if not allowed:
            self._record_reject(reason)
            return None

        up_ask = tick["up_best_ask"]
        down_ask = tick["down_best_ask"]
        
        if up_ask >= 0.98 and down_ask >= 0.98:
            self._record_reject("价格≥0.98")
            return None

        up_spread = tick["up_spread"]
        down_spread = tick["down_spread"]
        if (up_spread and up_spread > CFG.MAX_SPREAD) or (down_spread and down_spread > CFG.MAX_SPREAD):
            self._record_reject("点差过大")
            return None

        if tick["up_ask_depth"] < CFG.MIN_BID_DEPTH_SHARES or tick["down_ask_depth"] < CFG.MIN_BID_DEPTH_SHARES:
            self._record_reject("盘口深度不足")
            return None

        imbalance = self._calc_imbalance(tick)
        if abs(imbalance) < CFG.IMBALANCE_THRESHOLD:
            self._record_reject("失衡度不足")
            return None

        up_move = self._token_move(tick, "up")
        down_move = self._token_move(tick, "down")
        if abs(up_move) < CFG.TOKEN_MOVE_THRESHOLD and abs(down_move) < CFG.TOKEN_MOVE_THRESHOLD:
            self._record_reject("代币移动不足")
            return None

        spot_move = self._spot_move(tick)
        if abs(spot_move) < CFG.SPOT_MOVE_THRESHOLD_BPS:
            self._record_reject("现货移动不足")
            return None

        direction = self._decide_direction(imbalance, up_move, down_move, spot_move)
        if direction is None:
            self._record_reject("方向不一致")
            return None

        token_id = tick["up_token_id"] if direction == "UP" else tick["down_token_id"]
        entry_ask = up_ask if direction == "UP" else down_ask

        if entry_ask >= 0.98:
            self._record_reject("最终价格≥0.98")
            return None

        shares = self._calc_position_size(entry_ask)
        if shares <= 0:
            self._record_reject("仓位为0")
            return None

        fee_est = self.calc_taker_fee(shares, entry_ask)
        cost = shares * entry_ask + fee_est

        # 成功开单，清除当前窗口的拒绝原因
        self.last_reject_reason = ""
        return Signal(
            direction=direction, token_id=token_id, entry_ask=entry_ask,
            shares=shares, cost=cost, fee_est=fee_est,
            market_slug=slug, up_token_id=tick["up_token_id"],
            down_token_id=tick["down_token_id"], market_end_ts=tick["market_end_ts"],
            reason=f"imb={imbalance:.3f} up_mv={up_move:.3f} dn_mv={down_move:.3f} spot={spot_move:.1f}bps",
        )

    @staticmethod
    def _calc_imbalance(tick: Dict[str, Any]) -> float:
        up_bid_n = tick.get("up_bid_notional", 0.0)
        up_ask_n = tick.get("up_ask_notional", 0.0)
        total = up_bid_n + up_ask_n
        return 0.0 if total < 1e-9 else (up_bid_n - up_ask_n) / total

    @staticmethod
    def _token_move(tick, side: str) -> float:
        if side == "up":
            price, open_price = tick.get("up_best_bid") or 0.0, tick.get("up_open_price") or 0.0
        else:
            price, open_price = tick.get("down_best_bid") or 0.0, tick.get("down_open_price") or 0.0
        return 0.0 if open_price < 1e-9 else price - open_price

    @staticmethod
    def _spot_move(tick: Dict[str, Any]) -> float:
        spot, spot_open = tick.get("spot_price", 0.0), tick.get("spot_open", 0.0)
        return 0.0 if spot_open < 1e-9 else (spot - spot_open) / spot_open * 10000.0

    @staticmethod
    def _decide_direction(imbalance, up_move, down_move, spot_move) -> Optional[str]:
        signals = []
        if imbalance > 0: signals.append("UP")
        elif imbalance < 0: signals.append("DOWN")
        if up_move > 0 and up_move > down_move: signals.append("UP")
        elif down_move > 0 and down_move > up_move: signals.append("DOWN")
        if spot_move > 0: signals.append("UP")
        elif spot_move < 0: signals.append("DOWN")
        if not signals: return None
        up_c, down_c = signals.count("UP"), signals.count("DOWN")
        return "UP" if up_c >= 2 else "DOWN" if down_c >= 2 else None

    @staticmethod
    def _calc_position_size(entry_ask: float) -> float:
        budget = min(CFG.ORDER_BUDGET_U, CFG.INIT_EQUITY * 0.5)
        if entry_ask < 0.01: return 0.0
        shares = budget / entry_ask
        return 0.0 if shares < CFG.MIN_TRADE_BUDGET_U / entry_ask else round(shares, 2)

    def update_account(self, pnl: float, win: bool, market_slug: str):
        acc = self.account
        acc.total_trades += 1; acc.trades_today += 1
        acc.daily_pnl += pnl; acc.total_pnl += pnl
        if win: acc.wins += 1; acc.consecutive_losses = 0
        else: acc.losses += 1; acc.consecutive_losses += 1
        acc.equity += pnl
        acc.peak_equity = max(acc.peak_equity, acc.equity)
        self.traded_windows.add(market_slug)
        self.last_trade_ts = time.time()
        self._save_state()
