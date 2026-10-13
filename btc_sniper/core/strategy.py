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
        return AccountState(
            equity=CFG.INIT_EQUITY,
            peak_equity=CFG.INIT_EQUITY,
            day=date.today().isoformat(),
        )

    def reset_daily(self):
        from datetime import date
        today = date.today().isoformat()
        if self.day != today:
            self.day = today
            self.daily_pnl = 0.0
            self.trades_today = 0
            self.consecutive_losses = 0


class Strategy:
    """
    核心策略：T-{entry_remain} 秒决策，止盈退出。
    """

    def __init__(self, entry_remain: int = None, state_suffix: str = ""):
        self.entry_remain = entry_remain if entry_remain is not None else CFG.DEFAULT_ENTRY_REMAIN
        self.state_suffix = state_suffix
        self.account = AccountState.fresh()
        self.traded_windows: set = set()
        self.last_trade_ts: float = 0.0
        self._load_state()

    # ---------- 状态 ----------

    @property
    def state_path(self) -> Path:
        name = CFG.STATE_FILE
        if self.state_suffix:
            name = f"state_{self.state_suffix}.json"
        return Path(CFG.DATA_DIR) / name

    def _load_state(self):
        path = self.state_path
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            acc_data = data.get("account", {})
            if acc_data:
                self.account = AccountState(**acc_data)
            self.traded_windows = set(data.get("traded_windows", []))
            self.account.reset_daily()
        except Exception:
            pass

    def _save_state(self):
        path = self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "account": asdict(self.account),
            "traded_windows": list(self.traded_windows)[-500:],
        }
        tmp = path.with_name(path.stem + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        for _ in range(10):
            try:
                tmp.replace(path)
                return
            except PermissionError:
                time.sleep(0.1)
        tmp.replace(path)

    # ---------- 手续费 ----------

    @staticmethod
    def calc_taker_fee(shares: float, price: float) -> float:
        gross = CFG.FEE_THETA * shares * price * (1.0 - price)
        return max(0.0, gross * (1.0 - CFG.TAKER_REBATE_RATE))

    # ---------- 风控 ----------

    def _check_risk(self, now_ts: int) -> Tuple[bool, str]:
        acc = self.account
        if acc.trades_today >= CFG.MAX_TRADES_PER_DAY:
            return False, "每日交易笔数已达上限"
        if acc.daily_pnl <= -CFG.DAILY_MAX_LOSS_U:
            return False, "每日最大亏损已触发"
        if acc.consecutive_losses >= CFG.MAX_CONSECUTIVE_LOSSES:
            return False, "连续亏损次数已达上限"
        if acc.equity < CFG.INIT_EQUITY * 0.05:
            return False, "账户权益过低"
        if now_ts - self.last_trade_ts < CFG.COOLDOWN_AFTER_TRADE_SEC:
            return False, "冷却中"
        return True, "OK"

    # ---------- 决策 ----------

    def evaluate(self, tick: Dict[str, Any]) -> Optional[Signal]:
        remain = tick["remain_sec"]
        now_ts = tick["ts"]

        # 条件0：进场时间（容差）
        if abs(remain - self.entry_remain) > CFG.ENTRY_TOLERANCE_SEC:
            return None

        slug = tick["market_slug"]
        if CFG.ONLY_ONE_TRADE_PER_WINDOW and slug in self.traded_windows:
            return None

        allowed, _ = self._check_risk(now_ts)
        if not allowed:
            return None

        up_ask = tick["up_best_ask"]
        down_ask = tick["down_best_ask"]

        up_valid = CFG.MIN_ENTRY_ASK <= up_ask <= CFG.MAX_ENTRY_ASK
        down_valid = CFG.MIN_ENTRY_ASK <= down_ask <= CFG.MAX_ENTRY_ASK
        if not up_valid and not down_valid:
            return None

        up_spread = tick["up_spread"]
        down_spread = tick["down_spread"]
        if (up_spread and up_spread > CFG.MAX_SPREAD) or (down_spread and down_spread > CFG.MAX_SPREAD):
            return None

        if tick["up_ask_depth"] < CFG.MIN_BID_DEPTH_SHARES:
            return None
        if tick["down_ask_depth"] < CFG.MIN_BID_DEPTH_SHARES:
            return None

        imbalance = self._calc_imbalance(tick)
        if abs(imbalance) < CFG.IMBALANCE_THRESHOLD:
            return None

        up_move = self._token_move(tick, "up")
        down_move = self._token_move(tick, "down")
        if abs(up_move) < CFG.TOKEN_MOVE_THRESHOLD and abs(down_move) < CFG.TOKEN_MOVE_THRESHOLD:
            return None

        spot_move = self._spot_move(tick)
        if abs(spot_move) < CFG.SPOT_MOVE_THRESHOLD_BPS:
            return None

        direction = self._decide_direction(imbalance, up_move, down_move, spot_move)
        if direction is None:
            return None

        token_id = tick["up_token_id"] if direction == "UP" else tick["down_token_id"]
        entry_ask = up_ask if direction == "UP" else down_ask

        shares = self._calc_position_size(entry_ask)
        if shares <= 0:
            return None

        fee_est = self.calc_taker_fee(shares, entry_ask)
        cost = shares * entry_ask + fee_est

        return Signal(
            direction=direction,
            token_id=token_id,
            entry_ask=entry_ask,
            shares=shares,
            cost=cost,
            fee_est=fee_est,
            market_slug=slug,
            up_token_id=tick["up_token_id"],
            down_token_id=tick["down_token_id"],
            market_end_ts=tick["market_end_ts"],
            reason=f"imb={imbalance:.3f} up_mv={up_move:.3f} dn_mv={down_move:.3f} spot={spot_move:.1f}bps",
        )

    # ---------- 辅助 ----------

    @staticmethod
    def _calc_imbalance(tick: Dict[str, Any]) -> float:
        up_bid_n = tick.get("up_bid_notional", 0.0)
        up_ask_n = tick.get("up_ask_notional", 0.0)
        total = up_bid_n + up_ask_n
        if total < 1e-9:
            return 0.0
        return (up_bid_n - up_ask_n) / total

    @staticmethod
    def _token_move(tick, side: str) -> float:
        if side == "up":
            price = tick.get("up_best_bid") or 0.0
            open_price = tick.get("up_open_price") or 0.0
        else:
            price = tick.get("down_best_bid") or 0.0
            open_price = tick.get("down_open_price") or 0.0
        if open_price < 1e-9:
            return 0.0
        return price - open_price

    @staticmethod
    def _spot_move(tick: Dict[str, Any]) -> float:
        spot = tick.get("spot_price", 0.0)
        spot_open = tick.get("spot_open", 0.0)
        if spot_open < 1e-9:
            return 0.0
        return (spot - spot_open) / spot_open * 10000.0

    @staticmethod
    def _decide_direction(imbalance, up_move, down_move, spot_move) -> Optional[str]:
        signals = []
        if imbalance > 0:
            signals.append("UP")
        elif imbalance < 0:
            signals.append("DOWN")

        if up_move > 0 and up_move > down_move:
            signals.append("UP")
        elif down_move > 0 and down_move > up_move:
            signals.append("DOWN")

        if spot_move > 0:
            signals.append("UP")
        elif spot_move < 0:
            signals.append("DOWN")

        if not signals:
            return None
        up_c = signals.count("UP")
        down_c = signals.count("DOWN")
        if up_c >= 2:
            return "UP"
        if down_c >= 2:
            return "DOWN"
        return None

    @staticmethod
    def _calc_position_size(entry_ask: float) -> float:
        budget = min(CFG.ORDER_BUDGET_U, CFG.INIT_EQUITY * 0.5)
        if entry_ask < 0.01:
            return 0.0
        shares = budget / entry_ask
        if shares < CFG.MIN_TRADE_BUDGET_U / entry_ask:
            return 0.0
        return round(shares, 2)

    # ---------- 账户更新 ----------

    def update_account(self, pnl: float, win: bool, market_slug: str):
        acc = self.account
        acc.total_trades += 1
        acc.trades_today += 1
        acc.daily_pnl += pnl
        acc.total_pnl += pnl
        if win:
            acc.wins += 1
            acc.consecutive_losses = 0
        else:
            acc.losses += 1
            acc.consecutive_losses += 1
        acc.equity += pnl
        acc.peak_equity = max(acc.peak_equity, acc.equity)
        self.traded_windows.add(market_slug)
        self.last_trade_ts = time.time()
        self._save_state()
