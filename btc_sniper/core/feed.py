# core/feed.py
import asyncio
import json
import time
import socket
import requests
import websockets
import requests.packages.urllib3.util.connection as urllib3_cn
from typing import Optional, List, Dict, Any, Tuple

from config.config import CFG

urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

class Level:
    __slots__ = ("price", "size")
    def __init__(self, price: float, size: float):
        self.price = price
        self.size = size

class Book:
    def __init__(self, token_id: str):
        self.token_id = token_id
        self.bids: List[Level] = []
        self.asks: List[Level] = []
        self.updated_at = 0.0

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else 0.99
    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else 0.01
    @property
    def spread(self) -> Optional[float]:
        return self.best_ask - self.best_bid
    @property
    def ask_depth(self) -> float:
        return sum(lv.size for lv in self.asks[:5]) if self.asks else 0.0
    @property
    def bid_notional(self) -> float:
        return sum(lv.price * lv.size for lv in self.bids)
    @property
    def ask_notional(self) -> float:
        return sum(lv.price * lv.size for lv in self.asks)

class Feed:
    def __init__(self):
        self.ws_url = CFG.WS_URL
        self.gamma_base = CFG.GAMMA_BASE_URL
        self.clob_base = CFG.CLOB_BASE_URL
        self.books: Dict[str, Book] = {}
        self.token_ids: List[str] = []
        self.market_slug: str = ""
        self.market_end_ts: int = 0
        self.spot_price: float = 0.0
        self.spot_open: float = 0.0
        self.up_open_price: float = 0.0
        self.down_open_price: float = 0.0
        self._ws = None
        self._running = False
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        self.last_market_check = 0

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

    def discover_market(self) -> bool:
        now = int(time.time())
        window_secs = CFG.WINDOW_SECONDS
        base = (now // window_secs) * window_secs
        if now - self.last_market_check < 5 and self.market_slug: return True
        self.last_market_check = now

        for offset in CFG.MARKET_SEARCH_OFFSETS:
            start = base + offset
            end = start + window_secs
            if end <= now - 3: continue
            slug = f"{CFG.MARKET_SLUG_PREFIX}-{start}"
            try:
                data = self._http_get(f"{self.gamma_base}/events", params={"slug": slug})
                events = data if isinstance(data, list) else [data]
                ev = events[0] if events else None
                if not ev: continue
                markets = ev.get("markets") or []
                if not markets: continue
                market = markets[0]
                up_id, down_id = self._parse_ids(market)
                if not up_id or not down_id: continue
                
                if self.market_slug != slug:
                    self.market_slug = slug
                    self.market_end_ts = end
                    self.token_ids = [up_id, down_id]
                    elapsed = now - start
                    print(f"[{time.strftime('%H:%M:%S')}] 发现新窗口: {slug} (elapsed={elapsed}s)")
                    self._fetch_spot()
                    self._rest_fetch_books()
                    self._set_open_prices()
                    for tid in self.token_ids:
                        if tid not in self.books: self.books[tid] = Book(token_id=tid)
                return True
            except Exception: continue
        return False

    def _set_open_prices(self):
        up_book = self.books.get(self.token_ids[0]) if len(self.token_ids) > 0 else None
        down_book = self.books.get(self.token_ids[1]) if len(self.token_ids) > 1 else None
        if up_book and up_book.best_ask: self.up_open_price = up_book.best_ask
        if down_book and down_book.best_ask: self.down_open_price = down_book.best_ask
        if self.spot_price > 0: self.spot_open = self.spot_price

    @staticmethod
    def _parse_ids(market: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        up_id = down_id = None
        tokens = market.get("tokens")
        if isinstance(tokens, list):
            for t in tokens:
                outcome = str(t.get("outcome", "")).lower()
                tid = t.get("token_id") or t.get("tokenId")
                if not tid: continue
                if outcome == "up": up_id = str(tid)
                elif outcome == "down": down_id = str(tid)
        if up_id and down_id: return up_id, down_id
        try:
            outcomes = json.loads(market.get("outcomes", "[]"))
            token_ids = json.loads(market.get("clobTokenIds", "[]"))
            for o, tid in zip(outcomes, token_ids):
                if str(o).lower() == "up": up_id = str(tid)
                elif str(o).lower() == "down": down_id = str(tid)
        except Exception: pass
        return up_id, down_id

    def _fetch_spot(self):
        try:
            data = self._http_get("https://api.binance.com/api/v3/ticker/price", params={"symbol": "BTCUSDT"}, timeout=3)
            self.spot_price = float(data["price"])
        except Exception: pass

    def _rest_fetch_books(self):
        if len(self.token_ids) < 2: return
        for tid in self.token_ids:
            try:
                data = self._http_get(f"{self.clob_base}/book", params={"token_id": tid}, timeout=CFG.REST_BOOK_TIMEOUT)
                book = Book(token_id=tid)
                book.bids = self._parse_levels(data.get("bids", []), reverse=True)
                book.asks = self._parse_levels(data.get("asks", []), reverse=False)
                book.updated_at = time.time()
                self.books[tid] = book
                print(f"[{time.strftime('%H:%M:%S')}] REST 拉取盘口成功: {tid[:10]}... 买一={book.best_bid} 卖一={book.best_ask}")
            except Exception as e:
                print(f"[{time.strftime('%H:%M:%S')}] ⚠️ REST 拉取盘口失败: {tid[:10]}... 原因: {e}")

    @staticmethod
    def _parse_levels(raw, reverse: bool) -> List[Level]:
        out = []
        if not isinstance(raw, list): return out
        for x in raw:
            try:
                if isinstance(x, dict): p, s = x.get("price"), x.get("size")
                elif isinstance(x, (list, tuple)): p, s = x[0], x[1]
                else: continue
                p_f, s_f = float(p), float(s)
                if p_f > 0 and s_f > 0: out.append(Level(price=p_f, size=s_f))
            except Exception: continue
        out.sort(key=lambda z: z.price, reverse=reverse)
        return out

    async def _ws_loop(self):
        while self._running:
            try:
                print(f"[{time.strftime('%H:%M:%S')}] 尝试连接 WebSocket...")
                async with websockets.connect(self.ws_url, ping_interval=CFG.WS_PING_INTERVAL, ping_timeout=CFG.WS_PING_TIMEOUT) as ws:
                    self._ws = ws
                    sub = {"assets_ids": self.token_ids, "type": "market", "custom_feature_enabled": True}
                    await ws.send(json.dumps(sub))
                    print(f"[{time.strftime('%H:%M:%S')}] WebSocket 已订阅")
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                            self._handle_ws_message(msg)
                        except json.JSONDecodeError: pass
            except Exception as e:
                print(f"[{time.strftime('%H:%M:%S')}] WebSocket 断开或异常: {repr(e)}")
                if not self._running: break
                await asyncio.sleep(CFG.WS_RECONNECT_BASE)

    def _handle_ws_message(self, msg):
        if isinstance(msg, list):
            for item in msg: self._handle_ws_message(item)
            return
        if not isinstance(msg, dict): return
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
                if tid not in self.books: continue
                book = self.books[tid]
                side = str(ch.get("side", "")).upper()
                price = float(ch.get("price", 0))
                size = float(ch.get("size", 0))
                target = book.bids if side == "BUY" else book.asks
                target[:] = [lv for lv in target if abs(lv.price - price) > 1e-9]
                if size > 1e-9: target.append(Level(price=price, size=size))
                target.sort(key=lambda z: z.price, reverse=(side == "BUY"))
                book.updated_at = time.time()
        elif event_type == "best_bid_ask":
            tid = msg.get("asset_id")
            if tid not in self.books: return
            book = self.books[tid]
            bid, ask = msg.get("best_bid"), msg.get("best_ask")
            if bid: book.bids = [Level(price=float(bid), size=0.0)]
            if ask: book.asks = [Level(price=float(ask), size=0.0)]
            book.updated_at = time.time()

    async def start(self):
        self._running = True
        for _ in range(10):
            if self.discover_market(): break
            await asyncio.sleep(2)
        if not self.token_ids: raise RuntimeError("无法发现 BTC 5m 市场")
        self._fetch_spot()
        self._rest_fetch_books()
        await asyncio.gather(self._ws_loop(), self._rest_fallback_loop(), self._spot_loop())

    async def _rest_fallback_loop(self):
        while self._running:
            await asyncio.sleep(CFG.REST_BOOK_FALLBACK_INTERVAL_SEC)
            if not CFG.REST_BOOK_FALLBACK_ENABLED: continue
            now = time.time()
            for book in self.books.values():
                if now - book.updated_at > CFG.BOOK_STALE_SEC:
                    self._rest_fetch_books(); break

    async def _spot_loop(self):
        while self._running:
            self._fetch_spot()
            await asyncio.sleep(1.0)

    def stop(self): self._running = False

    def get_tick(self) -> Optional[Dict[str, Any]]:
        if len(self.token_ids) < 2: return None
        
        # === 核心修复：窗口过期强制熔断 ===
        now = int(time.time())
        if now > self.market_end_ts + 5:
            print(f"[{time.strftime('%H:%M:%S')}] ⚠️ 窗口已过期！强制重置市场信息，触发下一轮发现...")
            self.market_slug = ""
            self.token_ids = []
            self.books = {}
            return None
        # =================================

        up_id, down_id = self.token_ids
        up_book = self.books.get(up_id)
        down_book = self.books.get(down_id)
        
        if not up_book or not down_book: return None
            
        return {
            "ts": now, "market_slug": self.market_slug, "market_end_ts": self.market_end_ts,
            "remain_sec": max(0, self.market_end_ts - now),
            "up_token_id": up_id, "down_token_id": down_id,
            "up_best_bid": up_book.best_bid, "up_best_ask": up_book.best_ask,
            "up_spread": up_book.spread, "up_ask_depth": up_book.ask_depth,
            "down_best_bid": down_book.best_bid, "down_best_ask": down_book.best_ask,
            "down_spread": down_book.spread, "down_ask_depth": down_book.ask_depth,
            "up_bid_notional": up_book.bid_notional, "up_ask_notional": up_book.ask_notional,
            "down_bid_notional": down_book.bid_notional, "down_ask_notional": down_book.ask_notional,
            "up_open_price": self.up_open_price, "down_open_price": self.down_open_price,
            "spot_price": self.spot_price, "spot_open": self.spot_open, "spot_updated_at": time.time(),
        }
