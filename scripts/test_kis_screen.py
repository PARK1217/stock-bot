"""KIS 일봉 → 스크리너 검증. 국내 ETF가 예측 파이프라인에 들어가는지 확인."""
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from bot.config import settings        # noqa: E402
from bot.screener import score_symbol  # noqa: E402

HOST = "https://openapivts.koreainvestment.com:29443"
c = httpx.Client(base_url=HOST, timeout=15.0)
tok = c.post("/oauth2/tokenP", json={"grant_type": "client_credentials",
             "appkey": settings.kis_paper_app_key,
             "appsecret": settings.kis_paper_app_secret}).json()["access_token"]


def hdr(tr):
    return {"authorization": f"Bearer {tok}", "appkey": settings.kis_paper_app_key,
            "appsecret": settings.kis_paper_app_secret, "tr_id": tr, "custtype": "P"}


def candles(sym):
    end = datetime.now()
    start = end - timedelta(days=200)
    for _ in range(6):
        time.sleep(0.7)
        r = c.get("/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                  headers=hdr("FHKST03010100"),
                  params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": sym,
                          "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                          "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                          "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
        j = r.json()
        if j.get("msg_cd") == "EGW00201":
            continue
        rows = j.get("output2", []) or []
        cl = [{"close": float(x["stck_clpr"])} for x in
              sorted(rows, key=lambda z: z.get("stck_bsop_date", ""))
              if x.get("stck_clpr")]
        return cl
    return []


# 458730 TIGER 미국배당다우존스, 069500 KODEX200, 360750 TIGER 미국S&P500
for sym, name in [("458730", "TIGER 미국배당다우존스"), ("069500", "KODEX 200"),
                  ("360750", "TIGER 미국S&P500")]:
    cl = candles(sym)
    r = score_symbol(sym, cl)
    if r:
        print(f"{sym} {name}: {len(cl)}봉  점수 {r.score:.1f}  "
              f"1M {r.ret_1m:+.1f}% 3M {r.ret_3m:+.1f}%  "
              f"{'정배열' if r.trend_aligned else ''}  @{r.last:,.0f}")
    else:
        print(f"{sym} {name}: 데이터 부족({len(cl)}봉)")
