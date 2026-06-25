"""통합증거금 총자산 계산 확인 — 국내잔고 vs 해외 present-balance 합계."""
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


def h(tr):
    return {"authorization": f"Bearer {at}", "appkey": KEY, "appsecret": SEC,
            "tr_id": tr, "custtype": "P"}


# 국내 잔고
import time
time.sleep(0.7)
d = c.get("/uapi/domestic-stock/v1/trading/inquire-balance", headers=h("VTTC8434R"),
          params={"CANO": CANO, "ACNT_PRDT_CD": PROD, "AFHR_FLPR_YN": "N", "OFL_YN": "",
                  "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
                  "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
                  "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}).json()
ds = (d.get("output2") or [{}])[0]
print("국내: 예수금", ds.get("dnca_tot_amt"), "| 총평가", ds.get("tot_evlu_amt"),
      "| 순자산", ds.get("nass_amt"))

# 해외 present-balance
time.sleep(0.7)
o = c.get("/uapi/overseas-stock/v1/trading/inquire-present-balance", headers=h("VTRP6504R"),
          params={"CANO": CANO, "ACNT_PRDT_CD": PROD, "WCRC_FRCR_DVSN_CD": "02",
                  "NATN_CD": "840", "TR_MKET_CD": "00", "INQR_DVSN_CD": "00"}).json()
o3 = o.get("output3") or {}
if isinstance(o3, list):
    o3 = o3[0] if o3 else {}
print("해외 output3: tot_asst_amt", o3.get("tot_asst_amt"), "| frcr_evlu_tota",
      o3.get("frcr_evlu_tota"), "| evlu_amt_smtl", o3.get("evlu_amt_smtl"))
print("해외보유:")
for r in o.get("output1", []):
    print(" ", r.get("pdno"), r.get("prdt_name", "")[:10], "수량", r.get("cblc_qty13"),
          "평가USD", r.get("frcr_evlu_amt2"), "수익률", r.get("evlu_pfls_rt1"))
