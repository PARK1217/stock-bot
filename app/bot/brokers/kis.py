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

    def __init__(self, account: tuple[str, str] | None = None,
                 paper: bool | None = None):
        # account=(CANO,PRDT) 지정 시 해당 계좌. paper=True면 TRADING_MODE 무관 모의 강제
        # (모의 자동매매가 실전모드에서도 실거래 안 되도록 안전장치).
        self.paper = settings.is_paper if paper is None else paper
        self.host = PAPER_HOST if self.paper else LIVE_HOST
        if self.paper:
            self._key = settings.kis_paper_app_key
            self._sec = settings.kis_paper_app_secret
            default = (settings.kis_paper_account_no, settings.kis_paper_account_prod)
        else:
            self._key = settings.kis_live_app_key
            self._sec = settings.kis_live_app_secret
            default = (settings.kis_live_account_no, settings.kis_live_account_prod)
        self.cano, self.prod = account or default
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
                "appkey": self._key,
                "appsecret": self._sec,
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
            "appkey": self._key,
            "appsecret": self._sec,
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
                "appkey": self._key,
                "appsecret": self._sec,
            },
            json=body,
        )
        resp.raise_for_status()
        return resp.json()["HASH"]

    def _get(self, path: str, headers: dict, params: dict):
        """초당거래 초과(EGW00201) 재시도 + 만료토큰(EGW00123) 자가복구.
        KIS는 만료 토큰에 HTTP 500+EGW00123을 주므로, 캐시 폐기 후 새 토큰으로 재시도."""
        r = None
        for _ in range(5):
            r = self._client.get(path, headers=headers, params=params)
            try:
                mc = r.json().get("msg_cd")
            except ValueError:
                mc = None
            if mc == "EGW00201":                     # 초당거래 초과
                time.sleep(0.7)
                continue
            if mc == "EGW00123":                     # 만료 토큰 → 캐시 폐기·갱신 후 재시도
                self._redis.delete(self._token_key)
                headers = {**headers, "authorization": f"Bearer {self._access_token()}"}
                continue
            return r
        return r

    @staticmethod
    def _rate_limited(data: dict) -> bool:
        """초당거래 초과(EGW00201 / msg1 '초당...') 판정."""
        return (data.get("msg_cd") == "EGW00201"
                or "초당" in (data.get("msg1") or ""))

    def _rate_limited_exc(self, exc: Exception) -> bool:
        """예외(주로 _hashkey의 5xx)가 레이트리밋인지."""
        resp = getattr(exc, "response", None)
        if resp is None:
            return False
        try:
            return self._rate_limited(resp.json())
        except Exception:  # noqa: BLE001
            return False

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

    # ---------- 해외(미국) ----------
    def get_overseas_balance(self) -> Balance:
        """해외 잔고(체결 반영). inquire-balance(VTTS3012R/TTTS3012R) 사용.
        ※ present-balance(VTRP6504R)는 모의서 체결수량을 0으로 반환해 보유 누락 →
          inquire-balance가 ovrs_cblc_qty로 실보유를 정확히 반환(체결 즉시 반영).
          거래소 필터가 모의서 무시되어 전체를 반환하므로 심볼로 머지·중복제거."""
        tr = "VTTS3012R" if self.paper else "TTTS3012R"
        merged: dict[str, Position] = {}
        for exc in ("NASD", "NYSE", "AMEX"):
            resp = self._get(
                "/uapi/overseas-stock/v1/trading/inquire-balance",
                self._headers(tr),
                {"CANO": self.cano, "ACNT_PRDT_CD": self.prod, "OVRS_EXCG_CD": exc,
                 "TR_CRCY_CD": "USD", "CTX_AREA_FK200": "", "CTX_AREA_NK200": ""})
            resp.raise_for_status()
            body = resp.json()
            # 계좌레벨 에러(예: 미니스탁/소수점 INVALID_CHECK_ACNO)는 거래소 반복 무의미 → 중단
            if body.get("rt_cd") not in ("0", None):
                break
            for r in body.get("output1", []) or []:
                sym = r.get("ovrs_pdno", "")
                qty = float(r.get("ovrs_cblc_qty") or 0)
                if not sym or qty <= 0 or sym in merged:
                    continue
                merged[sym] = Position(
                    symbol=sym, name=r.get("ovrs_item_name", ""), qty=qty,
                    avg_price=float(r.get("pchs_avg_pric") or 0),
                    current_price=float(r.get("now_pric2") or 0),
                    currency="USD")
            time.sleep(0.2)
        return Balance(cash=0.0, total_eval=0.0, positions=list(merged.values()))

    def overseas_fills(self, start: str, end: str) -> dict[int, dict]:
        """해외 주문별 체결현황 {odno(int): {ord,ccld,nccs}}. 주문→실체결 추적용.
        inquire-ccnl(모의 VTTS3035R / 실전 TTTS3035R). start/end=YYYYMMDD."""
        tr = "VTTS3035R" if self.paper else "TTTS3035R"
        resp = self._get(
            "/uapi/overseas-stock/v1/trading/inquire-ccnl", self._headers(tr),
            {"CANO": self.cano, "ACNT_PRDT_CD": self.prod, "PDNO": "",
             "ORD_STRT_DT": start, "ORD_END_DT": end, "SLL_BUY_DVSN": "00",
             "CCLD_NCCS_DVSN": "00", "OVRS_EXCG_CD": "%", "SORT_SQN": "DS",
             "ORD_DT": "", "ORD_GNO_BRNO": "", "ODNO": "",
             "CTX_AREA_NK200": "", "CTX_AREA_FK200": ""})
        resp.raise_for_status()
        out: dict[int, dict] = {}
        for r in resp.json().get("output", []) or []:
            try:
                oid = int(r.get("odno") or 0)
            except (ValueError, TypeError):
                continue
            out[oid] = {"ord": float(r.get("ft_ord_qty") or r.get("ord_qty") or 0),
                        "ccld": float(r.get("ft_ccld_qty") or 0),
                        "nccs": float(r.get("nccs_qty") or 0),
                        "ccld_prc": float(r.get("ft_ccld_unpr3") or 0),   # 체결단가 USD
                        "ccld_amt": float(r.get("ft_ccld_amt3") or 0),    # 체결금액 USD
                        "ord_prc": float(r.get("ft_ord_unpr3") or 0)}     # 주문단가 USD
        return out

    def overseas_orders(self, start: str, end: str) -> list[dict]:
        """해외 주문/체결 원장(행 리스트). 매수·매도 전부 KIS 체결내역 그대로.
        inquire-ccnl(VTTS3035R/TTTS3035R). 최신순. start/end=YYYYMMDD."""
        tr = "VTTS3035R" if self.paper else "TTTS3035R"
        resp = self._get(
            "/uapi/overseas-stock/v1/trading/inquire-ccnl", self._headers(tr),
            {"CANO": self.cano, "ACNT_PRDT_CD": self.prod, "PDNO": "",
             "ORD_STRT_DT": start, "ORD_END_DT": end, "SLL_BUY_DVSN": "00",
             "CCLD_NCCS_DVSN": "00", "OVRS_EXCG_CD": "%", "SORT_SQN": "DS",
             "ORD_DT": "", "ORD_GNO_BRNO": "", "ODNO": "",
             "CTX_AREA_NK200": "", "CTX_AREA_FK200": ""})
        resp.raise_for_status()
        out = []
        for r in resp.json().get("output", []) or []:
            out.append({
                "dt": (r.get("ord_dt") or "") + (r.get("ord_tmd") or ""),  # YYYYMMDDHHMMSS
                "symbol": r.get("pdno", ""),
                "side": "buy" if r.get("sll_buy_dvsn_cd") == "02" else "sell",
                "ord_qty": float(r.get("ft_ord_qty") or 0),
                "ccld_qty": float(r.get("ft_ccld_qty") or 0),
                "nccs": float(r.get("nccs_qty") or 0),
                "price": float(r.get("ft_ccld_unpr3") or 0),
                "amt": float(r.get("ft_ccld_amt3") or 0)})
        return out

    def place_overseas_order(self, symbol: str, side: Side, qty: int, price: float,
                             exchange: str | None = None) -> OrderResult:
        """해외(미국) 지정가 주문. 거래소코드 자동탐색(NASD/NYSE/AMEX)+캐시."""
        tr = ("VTTT1002U" if side == Side.BUY else "VTTT1001U") if self.paper \
            else ("TTTT1002U" if side == Side.BUY else "TTTT1006U")
        cached = self._redis.get(f"kis:exch:{symbol}")
        excs = [exchange] if exchange else (
            [cached.decode()] if cached else ["NASD", "NYSE", "AMEX"])
        last = "주문 실패"
        for exc in excs:
            body = {"CANO": self.cano, "ACNT_PRDT_CD": self.prod, "OVRS_EXCG_CD": exc,
                    "PDNO": symbol, "ORD_QTY": str(int(qty)),
                    "OVRS_ORD_UNPR": f"{price:.2f}", "ORD_SVR_DVSN_CD": "0",
                    "ORD_DVSN": "00"}
            data = None
            for attempt in range(4):                       # 레이트리밋 백오프 재시도
                try:
                    hk = self._hashkey(body)
                    resp = self._client.post("/uapi/overseas-stock/v1/trading/order",
                                             headers=self._headers(tr, hashkey=hk), json=body)
                    data = resp.json()
                except (httpx.HTTPError, ValueError) as e:
                    last = str(e)
                    if self._rate_limited_exc(e) and attempt < 3:
                        time.sleep(0.6 * (attempt + 1)); continue
                    data = None; break
                if data.get("rt_cd") != "0" and self._rate_limited(data) and attempt < 3:
                    time.sleep(0.6 * (attempt + 1)); continue
                break
            if data and data.get("rt_cd") == "0":
                self._redis.set(f"kis:exch:{symbol}", exc, ex=604800)
                return OrderResult(ok=True, order_id=(data.get("output") or {}).get("ODNO"),
                                   message=data.get("msg1", ""), raw=data)
            if data is not None:
                last = data.get("msg1", "")               # 거래소별 거절(다음 거래소 시도)
            time.sleep(0.4)
        return OrderResult(ok=False, message=last)

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
        data = None
        for attempt in range(4):                          # 레이트리밋 백오프 재시도
            try:
                hashkey = self._hashkey(body)
                resp = self._client.post(
                    "/uapi/domestic-stock/v1/trading/order-cash",
                    headers=self._headers(tr_id, hashkey=hashkey),
                    json=body,
                )
                data = resp.json()  # KIS는 거절(장마감 등)도 본문에 msg1 담아 5xx로 줌
            except (httpx.HTTPError, ValueError) as e:
                if self._rate_limited_exc(e) and attempt < 3:
                    time.sleep(0.6 * (attempt + 1)); continue
                log.warning("KIS 주문 통신실패: %s", e)
                return OrderResult(ok=False, message=str(e))
            if data.get("rt_cd") != "0" and self._rate_limited(data) and attempt < 3:
                time.sleep(0.6 * (attempt + 1)); continue
            break

        ok = data.get("rt_cd") == "0"
        if not ok:
            log.info("KIS 주문 거절: %s", data.get("msg1"))
        return OrderResult(
            ok=ok,
            order_id=(data.get("output") or {}).get("ODNO"),
            message=data.get("msg1", ""),
            raw=data,
        )
