"""한국투자증권(KIS Developers) 어댑터.

참고: 엔드포인트/TR_ID는 공식 포털 기준이나, 실제 사용 전
apiportal.koreainvestment.com 문서로 한 번 더 검증할 것.
토큰은 Redis에 캐시 (KIS 토큰 유효기간 24h, 잦은 재발급 시 제한).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta

import httpx
import redis

from bot.brokers.base import BrokerAdapter, Balance, OrderResult, Position, Side
from bot.config import settings

log = logging.getLogger(__name__)

PAPER_HOST = "https://openapivts.koreainvestment.com:29443"
LIVE_HOST = "https://openapi.koreainvestment.com:9443"

# TR_ID: 모의 vs 실전이 다르다
TR = {
    "price": "FHKST01010100",                       # 현재가 (공통)
    "balance": ("VTTC8434R", "TTTC8434R"),          # 잔고 (모의, 실전)
    "buy": ("VTTC0802U", "TTTC0802U"),              # 현금매수
    "sell": ("VTTC0801U", "TTTC0801U"),             # 현금매도
}


class KISBroker(BrokerAdapter):
    name = "kis"

    def __init__(self, account: tuple[str, str] | None = None):
        # account=(CANO, PRDT) 지정 시 해당 계좌 조회(통합조회용). 미지정 시 기본 계좌.
        self.paper = settings.is_paper
        self.host = PAPER_HOST if self.paper else LIVE_HOST
        self.cano, self.prod = account or (settings.kis_account_no,
                                           settings.kis_account_prod)
        self._redis = redis.from_url(settings.redis_url)
        self._client = httpx.Client(base_url=self.host, timeout=10.0)
        self._token_key = f"kis:token:{'paper' if self.paper else 'live'}"

    # ---------- auth ----------
    def _access_token(self) -> str:
        cached = self._redis.get(self._token_key)
        if cached:
            return cached.decode()

        resp = self._client.post(
            "/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": settings.kis_app_key,
                "appsecret": settings.kis_app_secret,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["access_token"]
        # 만료보다 짧게 캐시 (안전마진 1h)
        ttl = int(data.get("expires_in", 86400)) - 3600
        self._redis.set(self._token_key, token, ex=max(ttl, 60))
        return token

    def _headers(self, tr_id: str, *, hashkey: str | None = None) -> dict:
        h = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._access_token()}",
            "appkey": settings.kis_app_key,
            "appsecret": settings.kis_app_secret,
            "tr_id": tr_id,
            "custtype": "P",  # 개인
        }
        if hashkey:
            h["hashkey"] = hashkey
        return h

    def _hashkey(self, body: dict) -> str:
        resp = self._client.post(
            "/uapi/hashkey",
            headers={
                "content-type": "application/json; charset=utf-8",
                "appkey": settings.kis_app_key,
                "appsecret": settings.kis_app_secret,
            },
            json=body,
        )
        resp.raise_for_status()
        return resp.json()["HASH"]

    def _get(self, path: str, headers: dict, params: dict):
        """초당거래 초과(EGW00201) 시 간격두고 재시도."""
        r = None
        for _ in range(5):
            r = self._client.get(path, headers=headers, params=params)
            try:
                if r.json().get("msg_cd") == "EGW00201":
                    time.sleep(0.7)
                    continue
            except ValueError:
                pass
            return r
        return r

    # ---------- market ----------
    def get_price(self, symbol: str) -> float:
        resp = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            self._headers(TR["price"]),
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
        )
        resp.raise_for_status()
        return float(resp.json()["output"]["stck_prpr"])

    def get_candles(self, symbol: str, interval: str = "1d",
                    count: int = 100) -> list[dict]:
        """국내 일봉. 반환 [{ts,open,high,low,close,volume}] 과거→최근."""
        end = datetime.now()
        start = end - timedelta(days=int(count * 1.6) + 10)
        resp = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            self._headers("FHKST03010100"),
            {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol,
             "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
             "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
             "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"},
        )
        resp.raise_for_status()
        rows = resp.json().get("output2", []) or []
        out = []
        for r in rows:
            if not r.get("stck_clpr"):
                continue
            out.append({
                "ts": r.get("stck_bsop_date"),
                "open": float(r.get("stck_oprc") or 0),
                "high": float(r.get("stck_hgpr") or 0),
                "low": float(r.get("stck_lwpr") or 0),
                "close": float(r.get("stck_clpr") or 0),
                "volume": float(r.get("acml_vol") or 0),
            })
        out.sort(key=lambda c: c["ts"] or "")  # 과거→최근
        return out

    # ---------- account ----------
    def get_balance(self) -> Balance:
        tr_id = TR["balance"][0 if self.paper else 1]
        resp = self._get(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            self._headers(tr_id),
            {
                "CANO": self.cano,
                "ACNT_PRDT_CD": self.prod,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        positions = [
            Position(
                symbol=row["pdno"],
                name=row["prdt_name"],
                qty=int(row["hldg_qty"]),
                avg_price=float(row["pchs_avg_pric"]),
                current_price=float(row["prpr"]),
            )
            for row in data.get("output1", [])
            if int(row.get("hldg_qty", 0)) > 0
        ]
        summary = (data.get("output2") or [{}])[0]
        return Balance(
            cash=float(summary.get("dnca_tot_amt", 0)),
            total_eval=float(summary.get("tot_evlu_amt", 0)),
            positions=positions,
        )

    # ---------- order ----------
    def place_order(self, symbol: str, side: Side, qty: int,
                    price: float | None = None) -> OrderResult:
        tr_id = TR["buy" if side == Side.BUY else "sell"][0 if self.paper else 1]
        # 시장가 = ord_dvsn "01", 지정가 = "00"
        ord_dvsn = "01" if price is None else "00"
        body = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.prod,
            "PDNO": symbol,
            "ORD_DVSN": ord_dvsn,
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0" if price is None else str(int(price)),
        }
        try:
            hashkey = self._hashkey(body)
            resp = self._client.post(
                "/uapi/domestic-stock/v1/trading/order-cash",
                headers=self._headers(tr_id, hashkey=hashkey),
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            log.exception("KIS order failed")
            return OrderResult(ok=False, message=str(e))

        ok = data.get("rt_cd") == "0"
        return OrderResult(
            ok=ok,
            order_id=(data.get("output") or {}).get("ODNO"),
            message=data.get("msg1", ""),
            raw=data,
        )
