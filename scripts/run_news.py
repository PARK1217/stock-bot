"""실제 bot.news(Finnhub 자동수집)+bot.forecast 로 뉴스반영 예측 데모.
실행: python scripts/run_news.py NVDA 21   (프로젝트 루트에서)"""
from __future__ import annotations

import base64
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from bot.config import settings          # noqa: E402  (.env 로드)
from bot.news import get_sentiment       # noqa: E402  (Finnhub 실수집)
from bot.forecast import forecast_symbol  # noqa: E402
from bot.screener import score_symbol    # noqa: E402

SYM = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
HORIZON = int(sys.argv[2]) if len(sys.argv) > 2 else 21
HOST = "https://openapi.tossinvest.com"

# --- 토스 캔들(시세) ---
c = httpx.Client(base_url=HOST, timeout=20.0)
basic = base64.b64encode(
    f"{settings.toss_app_key}:{settings.toss_app_secret}".encode()).decode()
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

# --- 뉴스 감성 (Finnhub 자동) ---
print(f"== {SYM} 뉴스 감성 (Finnhub 자동수집) ==")
sent = get_sentiment(SYM, "US")
print(f"점수 {sent.score:+.2f} / 신뢰도 {sent.confidence:.2f} / 기사 {sent.sources}건")
print(f"요약: {sent.summary}")
for it in sent.items[:5]:
    print(f"  · {it.headline[:80]}")
news_tilt = sent.score * sent.confidence

# --- 예측 (캔들만 vs 캔들+뉴스) ---
sc = score_symbol(SYM, [{"close": x} for x in closes])
tech = max(-1.0, min(1.0, sc.score / 25.0)) if sc else 0.0
fc0 = forecast_symbol(SYM, closes, HORIZON, technical_tilt=tech, news_sentiment=0.0)
fc1 = forecast_symbol(SYM, closes, HORIZON, technical_tilt=tech, news_sentiment=news_tilt)
print(f"\n뉴스 틸트 {news_tilt:+.3f} 반영")
if fc0 and fc1:
    print(f"  상승확률 {fc0.prob_up*100:.0f}% → {fc1.prob_up*100:.0f}%")
    print(f"  기대수익 {fc0.exp_return:+.1f}% → {fc1.exp_return:+.1f}%")
    print(f"  +5% 도달 {fc0.target_touch['+5%']*100:.0f}% → {fc1.target_touch['+5%']*100:.0f}%")
