"""KIS 모의 주문 엔드포인트 검증 (모의계좌, 안전). 069500 1주 지정가 매수 시도."""
import sys
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from bot.config import settings  # noqa: E402

HOST = "https://openapivts.koreainvestment.com:29443"
KEY, SEC = settings.kis_paper_app_key, settings.kis_paper_app_secret
CANO, PROD = settings.kis_paper_account_no, settings.kis_paper_account_prod
c = httpx.Client(base_url=HOST, timeout=15.0)

at = c.post("/oauth2/tokenP", json={"grant_type": "client_credentials",
            "appkey": KEY, "appsecret": SEC}).json()["access_token"]

body = {"CANO": CANO, "ACNT_PRDT_CD": PROD, "PDNO": "069500",
        "ORD_DVSN": "00", "ORD_QTY": "1", "ORD_UNPR": "10000"}  # 지정가 1만원(미체결)
# hashkey
hk = c.post("/uapi/hashkey", headers={"content-type": "application/json",
            "appkey": KEY, "appsecret": SEC}, json=body).json()["HASH"]
r = c.post("/uapi/domestic-stock/v1/trading/order-cash",
           headers={"authorization": f"Bearer {at}", "appkey": KEY, "appsecret": SEC,
                    "tr_id": "VTTC0802U", "custtype": "P", "hashkey": hk,
                    "content-type": "application/json"}, json=body)
print("HTTP", r.status_code)
j = r.json()
print("rt_cd:", j.get("rt_cd"), "| msg:", j.get("msg1"), "| ODNO:",
      (j.get("output") or {}).get("ODNO"))
print("(rt_cd=0=주문수락, 장마감/예약 메시지여도 엔드포인트·파라미터는 정상)")
