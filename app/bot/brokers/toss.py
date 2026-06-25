"""토스증권 Open API 어댑터.

✅ 공식 OpenAPI 스펙(https://openapi.tossinvest.com/openapi-docs/latest/openapi.json)
   기준으로 작성·실호출 검증 완료 (2026-06-25).

검증된 사실:
  - base: https://openapi.tossinvest.com,  경로 접두사: /api/v1
  - 토큰: POST /oauth2/token (Basic auth, client_credentials), 유효 약 24h
  - 응답 envelope: { "result": ... }, 모든 금액/수량은 문자열(string)
  - 계좌/자산/주문 API는 헤더 X-Tossinvest-Account = accountSeq(정수) 필요 (계좌번호 아님)
  - 현재가: GET /api/v1/prices?symbols=005930,000660 (콤마구분, 최대200) → result[].lastPrice
  - 잔고:   GET /api/v1/holdings → result.items[]
  - 매수가능금액: GET /api/v1/buying-power?currency=KRW → result.cashBuyingPower
  - 주문:   POST /api/v1/orders {symbol, side, orderType, quantity, price?, ...}

⚠️ 토스는 모의투자 환경이 없음 → 주문은 실거래. settings.toss_allow_live=True 일 때만 허용.
⚠️ 다중통화(KRW/USD) 평가금액 합산은 환율 필요 → TODO(/api/v1/exchange-rate).
"""
from __future__ import annotations

import base64
import logging
import time

import httpx
import redis

from bot.brokers.base import BrokerAdapter, Balance, OrderResult, Position, Side
from bot.config import settings

log = logging.getLogger(__name__)

HOST = "https://openapi.tossinvest.com"


class TossBroker(BrokerAdapter):
    name = "toss"

    def __init__(self):
        self.account_no = settings.toss_account_no
        self._redis = redis.from_url(settings.redis_url)
        self._client = httpx.Client(base_url=HOST, timeout=15.0)
        self._token_key = "toss:token"
        self._seq_key = f"toss:accseq:{self.account_no}"

    # ---------- auth ----------
    def _access_token(self) -> str:
        cached = self._redis.get(self._token_key)
        if cached:
            return cached.decode()
        basic = base64.b64encode(
            f"{settings.toss_app_key}:{settings.toss_app_secret}".encode()
        ).decode()
        resp = self._client.post(
            "/oauth2/token",
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials"},
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["access_token"]
        ttl = int(data.get("expires_in", 86400)) - 600
        self._redis.set(self._token_key, token, ex=max(ttl, 60))
        return token

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self._access_token()}"}

    def _account_seq(self) -> str:
        """X-Tossinvest-Account 헤더용 accountSeq. 계좌번호로 조회 후 캐시."""
        cached = self._redis.get(self._seq_key)
        if cached:
            return cached.decode()
        resp = self._client.get("/api/v1/accounts", headers=self._auth())
        resp.raise_for_status()
        accounts = resp.json().get("result", [])
        seq = None
        for a in accounts:
            if not self.account_no or str(a.get("accountNo")) == self.account_no:
                seq = str(a.get("accountSeq"))
                break
        if seq is None and accounts:
            seq = str(accounts[0].get("accountSeq"))  # 폴백: 첫 계좌
        if seq is None:
            raise RuntimeError("토스 계좌를 찾을 수 없습니다.")
        self._redis.set(self._seq_key, seq, ex=3600)
        return seq

    def _acc_headers(self) -> dict:
        return {**self._auth(), "X-Tossinvest-Account": self._account_seq()}

    def _get(self, path: str, params: dict, *, acc: bool = False):
        """레이트리밋(429) 백오프 + 토큰만료(401) 자동 재발급 GET."""
        resp = None
        for attempt in range(5):
            headers = self._acc_headers() if acc else self._auth()
            resp = self._client.get(path, headers=headers, params=params)
            if resp.status_code == 401 and attempt < 2:  # 토큰 무효 → 캐시삭제 후 재발급
                self._redis.delete(self._token_key)
                continue
            if resp.status_code != 429:
                break
            time.sleep(0.5 * (attempt + 1))
        return resp

    # ---------- market ----------
    def usdkrw(self) -> float:
        try:
            r = self._get("/api/v1/exchange-rate",
                          {"baseCurrency": "USD", "quoteCurrency": "KRW"})
            r.raise_for_status()
            res = r.json().get("result", {})
            return float(res.get("rate") or res.get("midRate") or 0)
        except (httpx.HTTPError, ValueError):
            log.warning("토스 환율 조회 실패 → 0 반환")
            return 0.0

    def get_price(self, symbol: str) -> float:
        resp = self._get("/api/v1/prices", {"symbols": symbol})
        resp.raise_for_status()
        result = resp.json().get("result", [])
        if not result:
            return 0.0
        return float(result[0].get("lastPrice") or 0)

    def get_prices(self, symbols: list[str]) -> dict[str, float]:
        """여러 종목 현재가 일괄(토스 /prices는 콤마 최대200). 1회 호출."""
        if not symbols:
            return {}
        resp = self._get("/api/v1/prices", {"symbols": ",".join(symbols[:200])})
        resp.raise_for_status()
        return {r.get("symbol"): float(r.get("lastPrice") or 0)
                for r in resp.json().get("result", [])}

    def get_stock_info(self, symbols: list[str]) -> list[dict]:
        """종목 기본정보(securityType·market·leverageFactor 등). 계좌 가능여부 판정용."""
        resp = self._get("/api/v1/stocks", {"symbols": ",".join(symbols[:200])})
        resp.raise_for_status()
        return resp.json().get("result", [])

    def get_candles(self, symbol: str, interval: str = "1d",
                    count: int = 120) -> list[dict]:
        """일/분봉. 반환: [{ts, open, high, low, close, volume}] 과거→최근 순."""
        resp = self._get("/api/v1/candles",
                         {"symbol": symbol, "interval": interval,
                          "count": min(count, 200), "adjusted": "true"})
        resp.raise_for_status()
        rows = resp.json().get("result", {}).get("candles", [])
        out = [{
            "ts": r.get("timestamp"),
            "open": float(r.get("openPrice") or 0),
            "high": float(r.get("highPrice") or 0),
            "low": float(r.get("lowPrice") or 0),
            "close": float(r.get("closePrice") or 0),
            "volume": float(r.get("volume") or 0),
        } for r in rows]
        out.sort(key=lambda c: c["ts"] or "")  # 과거→최근
        return out

    # ---------- account ----------
    def get_balance(self, currency: str = "KRW") -> Balance:
        # 현금(매수가능금액)
        cash = 0.0
        try:
            bp = self._get("/api/v1/buying-power", {"currency": currency}, acc=True)
            bp.raise_for_status()
            cash = float(bp.json().get("result", {}).get("cashBuyingPower") or 0)
        except httpx.HTTPError:
            log.warning("토스 매수가능금액 조회 실패")

        # 보유 종목
        resp = self._get("/api/v1/holdings", {}, acc=True)
        resp.raise_for_status()
        result = resp.json().get("result", {})
        positions: list[Position] = []
        for it in result.get("items", []):
            positions.append(Position(
                symbol=it.get("symbol", ""),
                name=it.get("name", ""),
                qty=float(it.get("quantity") or 0),
                avg_price=float(it.get("averagePurchasePrice") or 0),
                current_price=float(it.get("lastPrice") or 0),
                currency=it.get("currency", "KRW"),
            ))

        # 토스 overview가 전체·일간 수익률 직접 제공(ratio)
        def _rate(d):
            try:
                return round(float(d) * 100, 2)
            except (TypeError, ValueError):
                return None

        def _amt(d):
            try:
                return float(d)
            except (TypeError, ValueError):
                return None
        pl = result.get("profitLoss") or {}
        dpl = result.get("dailyProfitLoss") or {}
        total = cash + sum(p.market_value for p in positions)
        return Balance(cash=cash, total_eval=total, positions=positions,
                       total_pnl_pct=_rate(pl.get("rate")),
                       daily_pnl_pct=_rate(dpl.get("rate")),
                       total_pnl_amt=_amt((pl.get("amount") or {}).get("usd")),
                       daily_pnl_amt=_amt((dpl.get("amount") or {}).get("usd")))

    # ---------- order ----------
    def place_order(self, symbol: str, side: Side, qty: float,
                    price: float | None = None) -> OrderResult:
        if not settings.toss_allow_live:
            msg = "토스 실거래 차단됨 (TOSS_ALLOW_LIVE=true 로 명시 동의 필요)"
            log.warning(msg)
            return OrderResult(ok=False, message=msg)

        body = {
            "symbol": symbol,
            "side": "BUY" if side == Side.BUY else "SELL",
            "orderType": "MARKET" if price is None else "LIMIT",
            "quantity": str(qty),
            "timeInForce": "DAY",
        }
        if price is not None:
            body["price"] = str(int(price))

        try:
            resp = self._client.post("/api/v1/orders",
                                     headers={**self._acc_headers(),
                                              "Content-Type": "application/json"},
                                     json=body)
            resp.raise_for_status()
            data = resp.json().get("result", {})
        except httpx.HTTPError as e:
            log.exception("토스 주문 실패")
            return OrderResult(ok=False, message=str(e))

        return OrderResult(ok=True, order_id=str(data.get("orderId", "")),
                           message="ok", raw=data)
