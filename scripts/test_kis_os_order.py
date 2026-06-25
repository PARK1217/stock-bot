"""KIS 해외 모의 주문 엔드포인트 검증 — AAPL 1주 지정가 $100(미체결) 매수."""
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

body = {"CANO": CANO, "ACNT_PRDT_CD": PROD, "OVRS_EXCG_CD": "NASD",
        "PDNO": "AAPL", "ORD_QTY": "1", "OVRS_ORD_UNPR": "100",
        "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": "00"}
hk = c.post("/uapi/hashkey", headers={"content-type": "application/json",
            "appkey": KEY, "appsecret": SEC}, json=body).json()["HASH"]
for tr in ["VTTT1002U"]:  # 미국 매수 모의
    r = c.post("/uapi/overseas-stock/v1/trading/order",
               headers={"authorization": f"Bearer {at}", "appkey": KEY, "appsecret": SEC,
                        "tr_id": tr, "custtype": "P", "hashkey": hk,
                        "content-type": "application/json"}, json=body)
    j = r.json()
    print(f"[{tr}] HTTP {r.status_code} rt_cd={j.get('rt_cd')} msg={j.get('msg1')} "
          f"ODNO={(j.get('output') or {}).get('ODNO')}")
