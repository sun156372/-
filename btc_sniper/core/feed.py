# core/feed.py
import asyncio
import json
import time
import requests
import websockets
from typing import Optional, List, Dict, Any, Tuple

from config.config import CFG


# ==================== 数据结构 ====================

class Level:
    __slots__ = ("price", "size")
    def __init__(self, price: float, size: float):
        self.price = price
        self.size = size


class Book:
    def __init__(self, token_id: str):
        self.token_id = token_id
        self.bids: List[Level] = []   # 降序
        self.asks: List[Level] = []   # 升序
        self.updated_at = 0.0

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    @property
    def ask_depth(self) -> float:
        """前5档卖单总量"""
        return sum(lv.size for lv in self.asks[:5])

    @property
    def bid_notional(self) -> float:
        """买盘总名义价值 (Price * Size 之和)"""
        return sum(lv.price * lv.size for lv in self.bids)

    @property
    def ask_notional(self) -> float:
        """卖盘总名义价值 (Price * Size 之和)"""
        return sum(lv.price * lv.size for lv in self.asks)


# ==================== Feed 主类 ====================

class Feed:
    """
    行情获取：WebSocket 实时流 + REST 兜底快照
    - 记录窗口开盘价（Up/Down 代币、现货 BTC）
    - 输出买卖盘总名义价值，供策略计算失衡度
    """

    def __init__(self):
        self.ws_url = CFG.WS_URL
        self.gamma_base = CFG.GAMMA_BASE_URL
        self.clob_base = CFG.CLOB_BASE_URL

        # 盘口缓存 {token_id: Book}
        self.books: Dict[str, Book] = {}
        self.token_ids: List[str] = []
        self.market_slug: str = ""
        self.market_end_ts: int = 0

        # 现货 BTC 价格 (Binance 公开接口)
        self.spot_price: float = 0.0
        self.spot_open: float = 0.0
        self.spot_updated_at: float = 0.0

        # 代币开盘价
        self.up_open_price: float = 0.0
        self.down_open_price: float = 0.0

        # 内部状态
        self._ws = None
        self._running = False
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "polymarket-sniper/1.0"

    # ---------- HTTP 工具 ----------

    def _http_get(self, url: str, params: dict = None, timeout: int = 10) -> Any:
        for i in range(CFG.HTTP_RETRIES):
            try:
                r = self._session.get(url, params=params, timeout=timeout)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                if i == CFG.HTTP_RETRIES - 1:
                    raise RuntimeError(f"GET failed {url}: {e}")
                time.sleep(CFG.HTTP_RETRY_SLEEP * (i + 1))

    # ---------- 市场发现与开盘价记录 ----------

    def discover_market(self) -> bool:
        now = int(time.time())
        window_secs = CFG.WINDOW_SECONDS
        base = (now // window_secs) * window_secs

        for offset in CFG.MARKET_SEARCH_OFFSETS:
            start = base + offset
            end = start + window_secs
            if end <= now - 3:
                continue

            slug = f"{CFG.MARKET_SLUG_PREFIX}-{start}"
            try:
                data = self._http_get(f"{self.gamma_base}/events", params={"slug": slug})
                events = data if isinstance(data, list) else [data]
                ev = events[0] if events else None
                if not ev:
                    continue

                markets = ev.get("markets") or []
                if not markets:
                    continue

                market = markets[0]
                up_id, down_id = self._parse_ids(market)
                if not up_id or not down_id:
                    continue

                # 发现新窗口，重置开盘价
                if self.market_slug != slug:
                    self.market_slug = slug
                    self.market_end_ts = end
                    self.token_ids = [up_id, down_id]
                    self.up_open_price = 0.0
                    self.down_open_price = 0.0
                    self.spot_open = 0.0
                    # 立即拉取一次快照和现货作为开盘基准
                    self._fetch_spot()
                    self._rest_fetch_books()
                    self._set_open_prices()
                    # 初始化盘口缓存
                    for tid in self.token_ids:
                        if tid not in self.books:
                            self.books[tid] = Book(token_id=tid)
                return True

            except Exception:
                continue
        return False

    def _set_open_prices(self):
        """在窗口开始时记录代币和现货开盘价"""
        up_book = self.books.get(self.token_ids[0])
        down_book = self.books.get(self.token_ids[1])
        if up_book and up_book.best_ask:
            self.up_open_price = up_book.best_ask
        if down_book and down_book.best_ask:
            self.down_open_price = down_book.best_ask
        if self.spot_price > 0:
            self.spot_open = self.spot_price

    @staticmethod
    def _parse_ids(market: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        up_id = down_id = None
        tokens = market.get("tokens")
        if isinstance(tokens, list):
            for t in tokens:
                outcome = str(t.get("outcome", "")).lower()
                tid = t.get("token_id") or t.get("tokenId")
                if not tid:
                    continue
                if outcome == "up":
                    up_id = str(tid)
                elif outcome == "down":
                    down_id = str(tid)
        if up_id and down_id:
            return up_id, down_id

        try:
            outcomes = json.loads(market.get("outcomes", "[]"))
            token_ids = json.loads(market.get("clobTokenIds", "[]"))
            for o, tid in zip(outcomes, token_ids):
                if str(o).lower() == "up":
                    up_id = str(tid)
                elif str(o).lower() == "down":
                    down_id = str(tid)
        except Exception:
            pass
        return up_id, down_id

    # ---------- 现货 BTC 价格 ----------

    def _fetch_spot(self):
        try:
            data = self._http_get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "BTCUSDT"},
                timeout=3,
            )
            self.spot_price = float(data["price"])
            self.spot_updated_at = time.time()
        except Exception:
            pass

    # ---------- REST 盘口兜底 ----------

    def _rest_fetch_books(self):
        for tid in self.token_ids:
            try:
                data = self._http_get(
                    f"{self.clob_base}/book",
                    params={"token_id": tid},
                    timeout=CFG.REST_BOOK_TIMEOUT,
                )
                book = Book(token_id=tid)
                book.bids = self._parse_levels(data.get("bids", []), reverse=True)
                book.asks = self._parse_levels(data.get("asks", []), reverse=False)
                book.updated_at = time.time()
                self.books[tid] = book
            except Exception:
                pass

    @staticmethod
    def _parse_levels(raw, reverse: bool) -> List[Level]:
        out = []
        if not isinstance(raw, list):
            return out
        for x in raw:
            try:
                if isinstance(x, dict):
                    p, s = x.get("price"), x.get("size")
                elif isinstance(x, (list, tuple)):
                    p, s = x[0], x[1]
                else:
                    continue
                p_f, s_f = float(p), float(s)
                if p_f > 0 and s_f > 0:
                    out.append(Level(price=p_f, size=s_f))
            except Exception:
                continue
        out.sort(key=lambda z: z.price, reverse=reverse)
        return out

    # ---------- WebSocket 实时流 ----------

    async def _ws_loop(self):
        while self._running:
            try:
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=CFG.WS_PING_INTERVAL,
                    ping_timeout=CFG.WS_PING_TIMEOUT,
                ) as ws:
                    self._ws = ws
                    sub = {
                        "assets_ids": self.token_ids,
                        "type": "market",
                        "custom_feature_enabled": True,
                    }
                    await ws.send(json.dumps(sub))

                    async for raw in ws:
                        msg = json.loads(raw)
                        self._handle_ws_message(msg)

            except Exception:
                if not self._running:
                    break
                await asyncio.sleep(CFG.WS_RECONNECT_BASE)

    def _handle_ws_message(self, msg: Dict[str, Any]):
        event_type = msg.get("event_type", "")

        if event_type == "book":
            token_id = msg.get("asset_id")
            if token_id in self.books:
                book = self.books[token_id]
                book.bids = self._parse_levels(msg.get("bids", []), reverse=True)
                book.asks = self._parse_levels(msg.get("asks", []), reverse=False)
                book.updated_at = time.time()

        elif event_type == "price_change":
            for ch in msg.get("price_changes", []):
                tid = ch.get("asset_id")
                if tid not in self.books:
                    continue
                book = self.books[tid]
                side = str(ch.get("side", "")).upper()
                price = float(ch.get("price", 0))
                size = float(ch.get("size", 0))

                target = book.bids if side == "BUY" else book.asks
                target[:] = [lv for lv in target if abs(lv.price - price) > 1e-9]
                if size > 1e-9:
                    target.append(Level(price=price, size=size))
                reverse = side == "BUY"
                target.sort(key=lambda z: z.price, reverse=reverse)
                book.updated_at = time.time()

        elif event_type == "best_bid_ask":
            tid = msg.get("asset_id")
            if tid not in self.books:
                return
            book = self.books[tid]
            bid = msg.get("best_bid")
            ask = msg.get("best_ask")
            if bid:
                book.bids = [Level(price=float(bid), size=0.0)]
            if ask:
                book.asks = [Level(price=float(ask), size=0.0)]
            book.updated_at = time.time()

    # ---------- 启动与循环 ----------

    async def start(self):
        self._running = True

        for _ in range(10):
            if self.discover_market():
                break
            await asyncio.sleep(2)

        if not self.token_ids:
            raise RuntimeError("无法发现 BTC 5m 市场，请检查网络或参数")

        for tid in self.token_ids:
            if tid not in self.books:
                self.books[tid] = Book(token_id=tid)

        # 首次拉取
        self._fetch_spot()
        self._rest_fetch_books()

        await asyncio.gather(
            self._ws_loop(),
            self._rest_fallback_loop(),
            self._spot_loop(),
        )

    async def _rest_fallback_loop(self):
        while self._running:
            await asyncio.sleep(CFG.REST_BOOK_FALLBACK_INTERVAL_SEC)
            if not CFG.REST_BOOK_FALLBACK_ENABLED:
                continue
            now = time.time()
            for book in self.books.values():
                if now - book.updated_at > CFG.BOOK_STALE_SEC:
                    self._rest_fetch_books()
                    break

    async def _spot_loop(self):
        while self._running:
            self._fetch_spot()
            await asyncio.sleep(1.0)

    def stop(self):
        self._running = False

    # ---------- 对外输出 ----------

    def get_tick(self) -> Optional[Dict[str, Any]]:
        if len(self.token_ids) < 2:
            return None

        up_id, down_id = self.token_ids
        up_book = self.books.get(up_id)
        down_book = self.books.get(down_id)

        if not up_book or not down_book:
            return None
        if up_book.best_ask is None or down_book.best_ask is None:
            return None

        now = int(time.time())
        return {
            "ts": now,
            "market_slug": self.market_slug,
            "market_end_ts": self.market_end_ts,
            "remain_sec": max(0, self.market_end_ts - now),
            "up_token_id": up_id,
            "down_token_id": down_id,
            
            # ---- 盘口价格 ----
            "up_best_bid": up_book.best_bid,
            "up_best_ask": up_book.best_ask,
            "up_spread": up_book.spread,
            "up_ask_depth": up_book.ask_depth,
            "down_best_bid": down_book.best_bid,
            "down_best_ask": down_book.best_ask,
            "down_spread": down_book.spread,
            "down_ask_depth": down_book.ask_depth,
            
            # ---- 名义价值 (用于失衡度计算) ----
            "up_bid_notional": up_book.bid_notional,
            "up_ask_notional": up_book.ask_notional,
            "down_bid_notional": down_book.bid_notional,
            "down_ask_notional": down_book.ask_notional,
            
            # ---- 开盘价 (用于计算移动幅度) ----
            "up_open_price": self.up_open_price,
            "down_open_price": self.down_open_price,
            "spot_price": self.spot_price,
            "spot_open": self.spot_open,
            "spot_updated_at": self.spot_updated_at,
        }
