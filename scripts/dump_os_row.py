import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
from bot.config import settings  # noqa: E402
import httpx  # noqa: E402

H = "https://openapivts.koreainvestment.com:29443"
c = httpx.Client(base_url=H, timeout=15)
at = c.post("/oauth2/tokenP", json={"grant_type": "client_credentials",
            "appkey": settings.kis_paper_app_key,
            "appsecret": settings.kis_paper_app_secret}).json()["access_token"]
time.sleep(0.5)
o = c.get("/uapi/overseas-stock/v1/trading/inquire-present-balance",
          headers={"authorization": "Bearer " + at, "appkey": settings.kis_paper_app_key,
                   "appsecret": settings.kis_paper_app_secret, "tr_id": "VTRP6504R",
                   "custtype": "P"},
          params={"CANO": settings.kis_paper_account_no, "ACNT_PRDT_CD": "01",
                  "WCRC_FRCR_DVSN_CD": "02", "NATN_CD": "840", "TR_MKET_CD": "00",
                  "INQR_DVSN_CD": "00"}).json()
for r in o.get("output1", []):
    print(r.get("pdno"), {k: v for k, v in r.items()
                          if "qty" in k or "amt" in k or "rt1" in k})
