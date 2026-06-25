"""KIS 해외 체결기준현재잔고(present-balance) 구조 확인."""
import json
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

r = c.get("/uapi/overseas-stock/v1/trading/inquire-present-balance",
          headers={"authorization": f"Bearer {at}", "appkey": KEY, "appsecret": SEC,
                   "tr_id": "VTRP6504R", "custtype": "P"},
          params={"CANO": CANO, "ACNT_PRDT_CD": PROD, "WCRC_FRCR_DVSN_CD": "02",
                  "NATN_CD": "840", "TR_MKET_CD": "00", "INQR_DVSN_CD": "00"})
j = r.json()
print("HTTP", r.status_code, "rt_cd", j.get("rt_cd"), "msg", j.get("msg1"))
print("output1(보유) 수:", len(j.get("output1") or []))
if j.get("output1"):
    print("  보유 keys:", list(j["output1"][0])[:12])
for k in ["output2", "output3"]:
    o = j.get(k)
    if isinstance(o, list) and o:
        o = o[0]
    if o:
        print(f"  {k} keys:", list(o)[:14])
        # 예수금 후보
        for f in o:
            if "dnca" in f or "evlu" in f or "frcr" in f or "psamt" in f:
                print(f"     {f} = {o[f]}")
