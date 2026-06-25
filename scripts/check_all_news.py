"""보유 종목 전체에 대해 뉴스 관련성/감성 검증 (읽기전용).
각 종목: Finnhub 원본 → Groq 관련성필터 → 남은 헤드라인 + FinBERT 점수 표시.
사용자가 '진짜 관련 뉴스만 오는지' 직접 확인하는 용도."""
from __future__ import annotations

import base64
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from bot.config import settings                       # noqa: E402
from bot.news import FinnhubProvider                  # noqa: E402
from bot.sentiment import groq_relevant_indices, finbert_scores  # noqa: E402

HOST = "https://openapi.tossinvest.com"

# 토스 보유 종목 조회
c = httpx.Client(base_url=HOST, timeout=20.0)
basic = base64.b64encode(
    f"{settings.toss_app_key}:{settings.toss_app_secret}".encode()).decode()
tok = c.post("/oauth2/token", headers={"Authorization": f"Basic {basic}",
             "Content-Type": "application/x-www-form-urlencoded"},
             data={"grant_type": "client_credentials"}).json()["access_token"]
auth = {"Authorization": f"Bearer {tok}"}
seq = str(c.get("/api/v1/accounts", headers=auth).json()["result"][0]["accountSeq"])
acc = {**auth, "X-Tossinvest-Account": seq}
items = c.get("/api/v1/holdings", headers=acc).json()["result"]["items"]
holdings = [(h["symbol"], h.get("name", "")) for h in items]

print(f"보유 {len(holdings)}종목 뉴스 관련성/감성 검증 (Finnhub→Groq필터→FinBERT)\n")
provider = FinnhubProvider()

for sym, name in holdings:
    news = provider.fetch(sym, settings.news_lookback_days)
    total = len(news)
    if total == 0:
        print(f"■ {sym} ({name}): Finnhub 뉴스 0건 (ETF 등은 기사 적음)")
        continue
    heads = [n.headline for n in news]
    idx = groq_relevant_indices(sym, heads)
    relevant = [news[i] for i in idx if 0 <= i < total] if idx else []
    if idx is None:
        print(f"■ {sym} ({name}): Groq 필터 실패 → 원본 {total}건 (필터 미적용)")
        continue
    fb = finbert_scores([n.headline for n in relevant]) if relevant else None
    score = sum(s for _, s in fb) / len(fb) if fb else 0.0
    print(f"■ {sym} ({name}): 관련 {len(relevant)}/{total}건  감성 {score:+.2f}")
    for n in relevant:
        print(f"    · {n.headline[:90]}")
    if not relevant:
        print("    (관련 뉴스 없음 — 노이즈 전량 제거됨)")
    time.sleep(0.4)  # Groq RPM 여유
