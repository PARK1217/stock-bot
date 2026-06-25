"""KIS 실전 3계좌 통합잔고 검증 (읽기전용/주문없음). 주거용 IP에서 실행."""
import json
import sys
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from bot.config import settings  # noqa: E402

HOST = "https://openapi.koreainvestment.com:9443"  # 실전
KEY = settings.kis_live_app_key
SEC = settings.kis_live_app_secret
c = httpx.Client(base_url=HOST, timeout=15.0)

ACCOUNTS = [("63751874", "01", "소수점주식"),
            ("63776023", "01", "ISA중개형"),
            ("63776023", "22", "연금저축")]

tok = c.post("/oauth2/tokenP", json={"grant_type": "client_credentials",
             "appkey": KEY, "appsecret": SEC})
if tok.status_code != 200:
    print("토큰 실패:", tok.status_code, tok.text[:200]); sys.exit()
at = tok.json()["access_token"]


def balance(cano, prod):
    h = {"authorization": f"Bearer {at}", "appkey": KEY, "appsecret": SEC,
         "tr_id": "TTTC8434R", "custtype": "P"}  # 실전 잔고
    p = {"CANO": cano, "ACNT_PRDT_CD": prod, "AFHR_FLPR_YN": "N", "OFL_YN": "",
         "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
         "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
         "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}
    for _ in range(6):
        time.sleep(0.6)
        r = c.get("/uapi/domestic-stock/v1/trading/inquire-balance", headers=h, params=p)
        j = r.json()
        if j.get("msg_cd") == "EGW00201":
            continue
        return j
    return j


g_cash = g_eval = 0.0
print("=== KIS 실전 3계좌 통합잔고 (주거용 IP) ===")
for cano, prod, label in ACCOUNTS:
    j = balance(cano, prod)
    if j.get("rt_cd") != "0":
        print(f"\n[{label} {cano}-{prod}] 조회실패: {j.get('msg1') or j.get('msg_cd')}")
        continue
    summ = (j.get("output2") or [{}])[0]
    cash = float(summ.get("dnca_tot_amt", 0))
    ev = float(summ.get("tot_evlu_amt", 0))
    g_cash += cash; g_eval += ev
    rows = [x for x in j.get("output1", []) if int(x.get("hldg_qty", 0)) > 0]
    print(f"\n[{label} {cano}-{prod}] 현금 {cash:,.0f}  총평가 {ev:,.0f}  보유 {len(rows)}종")
    for x in rows[:8]:
        print(f"   {x['pdno']} {x['prdt_name'][:14]} {x['hldg_qty']}주 "
              f"({float(x.get('evlu_pfls_rt',0)):+.1f}%)")

print(f"\n═══ 통합: 현금 {g_cash:,.0f}  총평가 {g_eval:,.0f} KRW ═══")
