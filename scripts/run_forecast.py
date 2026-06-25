"""실데이터로 예측엔진 데모 (읽기전용). 사용: python scripts/run_forecast.py SCHD 21"""
from __future__ import annotations

import base64
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from bot.forecast import forecast_symbol  # noqa: E402
from bot.screener import score_symbol      # noqa: E402

HOST = "https://openapi.tossinvest.com"
SYM = sys.argv[1] if len(sys.argv) > 1 else "SCHD"
HORIZON = int(sys.argv[2]) if len(sys.argv) > 2 else 21


def load_env(p):
    env = {}
    for line in Path(p).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.split(" #")[0].strip()
    return env


env = load_env(ROOT / ".env")
c = httpx.Client(base_url=HOST, timeout=20.0)
basic = base64.b64encode(
    f"{env['TOSS_APP_KEY']}:{env['TOSS_APP_SECRET']}".encode()).decode()
tok = c.post("/oauth2/token", headers={"Authorization": f"Basic {basic}",
             "Content-Type": "application/x-www-form-urlencoded"},
             data={"grant_type": "client_credentials"}).json()["access_token"]
auth = {"Authorization": f"Bearer {tok}"}

for attempt in range(4):
    r = c.get("/api/v1/candles", headers=auth,
              params={"symbol": SYM, "interval": "1d", "count": 200, "adjusted": "true"})
    if r.status_code == 200:
        break
    time.sleep(0.5 * (attempt + 1))
closes = [float(x["closePrice"]) for x in
          sorted(r.json()["result"]["candles"], key=lambda z: z["timestamp"])
          if float(x["closePrice"]) > 0]

sc = score_symbol(SYM, [{"close": x} for x in closes])
tilt = max(-1.0, min(1.0, sc.score / 25.0)) if sc else 0.0
# argv[3] = 뉴스 감성 tilt(score*confidence). 데모용 실측 뉴스값 주입.
news = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
print(f"(기술적 틸트 {tilt:+.2f}, 뉴스 틸트 {news:+.2f})\n")

fc0 = forecast_symbol(SYM, closes, HORIZON, technical_tilt=tilt, news_sentiment=0.0)
print("【캔들만】")
print(fc0.report() if fc0 else "데이터 부족")
if news != 0.0:
    fc1 = forecast_symbol(SYM, closes, HORIZON, technical_tilt=tilt, news_sentiment=news)
    print("\n【캔들 + 뉴스】")
    print(fc1.report() if fc1 else "데이터 부족")
    if fc0 and fc1:
        print(f"\n→ 변화: 상승확률 {fc0.prob_up*100:.0f}%→{fc1.prob_up*100:.0f}%, "
              f"기대수익 {fc0.exp_return:+.1f}%→{fc1.exp_return:+.1f}%, "
              f"+5%도달 {fc0.target_touch['+5%']*100:.0f}%→{fc1.target_touch['+5%']*100:.0f}%")
