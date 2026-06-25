"""KIS 모의 해외(미국) 지원 확인 — 해외잔고·현재가 조회(읽기전용)."""
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


def hdr(tr):
    return {"authorization": f"Bearer {at}", "appkey": KEY, "appsecret": SEC,
            "tr_id": tr, "custtype": "P"}


# 1) 해외 모의 잔고 (VTTS3012R)
r = c.get("/uapi/overseas-stock/v1/trading/inquire-balance", headers=hdr("VTTS3012R"),
          params={"CANO": CANO, "ACNT_PRDT_CD": PROD, "OVRS_EXCG_CD": "NASD",
                  "TR_CRCY_CD": "USD", "CTX_AREA_FK200": "", "CTX_AREA_NK200": ""})
j = r.json()
print(f"[해외잔고 VTTS3012R] HTTP {r.status_code} rt_cd={j.get('rt_cd')} msg={j.get('msg1')}")
if j.get("rt_cd") == "0":
    s = (j.get("output2") or {})
    print("   예수금(USD):", s.get("frcr_pchs_amt1") or s.get("tot_evlu_pfls_amt"), "| output2 keys:", list(s)[:6])

# 2) 해외 현재가 (실시간/지연) — AAPL
r2 = c.get("/uapi/overseas-price/v1/quotations/price", headers=hdr("HHDFS00000300"),
           params={"AUTH": "", "EXCD": "NAS", "SYMB": "AAPL"})
j2 = r2.json()
print(f"[해외현재가] HTTP {r2.status_code} rt_cd={j2.get('rt_cd')} last={ (j2.get('output') or {}).get('last') }")
