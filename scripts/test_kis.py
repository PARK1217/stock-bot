"""KIS 모의투자 API 연결 검증 (읽기전용/주문없음). 토큰·현재가·일봉·잔고."""
import json
import sys
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from bot.config import settings  # noqa: E402

HOST = "https://openapivts.koreainvestment.com:29443"  # 모의투자
KEY = settings.kis_paper_app_key
SEC = settings.kis_paper_app_secret
CANO = settings.kis_paper_account_no  # 50194948
PROD = settings.kis_paper_account_prod
c = httpx.Client(base_url=HOST, timeout=15.0)


def show(t, r):
    print(f"\n=== {t}  HTTP {r.status_code} ===")
    try:
        print(json.dumps(r.json(), ensure_ascii=False)[:900])
    except Exception:
        print(r.text[:500])


def get_retry(path, headers, params):
    """초당거래 초과(EGW00201) 시 간격두고 재시도."""
    for a in range(5):
        time.sleep(0.7)
        r = c.get(path, headers=headers, params=params)
        try:
            if r.json().get("msg_cd") == "EGW00201":
                continue
        except Exception:
            pass
        return r
    return r


# 1) 토큰
tok = c.post("/oauth2/tokenP", json={"grant_type": "client_credentials",
             "appkey": KEY, "appsecret": SEC})
show("TOKEN /oauth2/tokenP", tok)
if tok.status_code != 200:
    sys.exit("토큰 실패")
at = tok.json()["access_token"]


def hdr(tr):
    return {"authorization": f"Bearer {at}", "appkey": KEY, "appsecret": SEC,
            "tr_id": tr, "custtype": "P"}


# 2) 현재가 (삼성전자)
show("PRICE inquire-price (005930)",
    c.get("/uapi/domestic-stock/v1/quotations/inquire-price",
          headers=hdr("FHKST01010100"),
          params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}))

# 3) 일봉 (기간별 시세)
dr = get_retry("/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
               hdr("FHKST03010100"),
               {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930",
                "FID_INPUT_DATE_1": "20260401", "FID_INPUT_DATE_2": "20260625",
                "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
show("DAILY inquire-daily-itemchartprice (005930)", dr)
try:
    o2 = dr.json().get("output2", [])
    print(f"  일봉 {len(o2)}개. 최근 3개:")
    for row in o2[:3]:
        print("   ", {k: row[k] for k in ("stck_bsop_date", "stck_clpr",
              "stck_oprc", "stck_hgpr", "stck_lwpr", "acml_vol") if k in row})
except Exception as e:
    print("파싱:", e)

# 4) 잔고 (모의 tr VTTC8434R)
show("BALANCE inquire-balance",
    c.get("/uapi/domestic-stock/v1/trading/inquire-balance",
          headers=hdr("VTTC8434R"),
          params={"CANO": CANO, "ACNT_PRDT_CD": PROD, "AFHR_FLPR_YN": "N",
                  "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01",
                  "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N",
                  "PRCS_DVSN": "00", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}))
