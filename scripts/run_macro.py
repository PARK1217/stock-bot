"""거시 신호 검증: TLT(금리)·SGOV·BOXX·GLDM 실데이터로 컨텍스트 확인."""
import base64
import sys
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from bot.config import settings           # noqa: E402
from bot.macro import rates_context, asset_context, RATE_PROXY  # noqa: E402
from bot.news import get_sentiment         # noqa: E402

HOST = "https://openapi.tossinvest.com"
c = httpx.Client(base_url=HOST, timeout=20.0)
basic = base64.b64encode(
    f"{settings.toss_app_key}:{settings.toss_app_secret}".encode()).decode()
tok = c.post("/oauth2/token", headers={"Authorization": f"Basic {basic}",
             "Content-Type": "application/x-www-form-urlencoded"},
             data={"grant_type": "client_credentials"}).json()["access_token"]
auth = {"Authorization": f"Bearer {tok}"}


def closes(sym):
    for a in range(4):
        r = c.get("/api/v1/candles", headers=auth,
                  params={"symbol": sym, "interval": "1d", "count": 60, "adjusted": "true"})
        if r.status_code == 200:
            rows = r.json()["result"]["candles"]
            return [float(x["closePrice"]) for x in
                    sorted(rows, key=lambda z: z["timestamp"]) if float(x["closePrice"]) > 0]
        time.sleep(0.5 * (a + 1))
    return []


tlt = closes(RATE_PROXY)
print(f"금리 프록시 {RATE_PROXY}: {len(tlt)}봉")
direction, desc = rates_context(tlt)
print(f"  금리 방향: {direction}  | {desc}\n")

for sym in ["SGOV", "BOXX", "GLDM"]:
    cl = closes(sym)
    ctx = asset_context(sym, cl, tlt)
    s = get_sentiment(sym, "US")
    print(f"■ {sym}: {ctx}")
    print(f"   뉴스감성(정직): score {s.score:+.2f} / {s.summary}")
