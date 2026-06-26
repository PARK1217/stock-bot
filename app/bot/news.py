"""뉴스/공시 감성 — 최근 N일 이슈를 수집·점수화해 예측 드리프트에 반영.

파이프라인(교체형):
  1) 수집 NewsProvider: 미국=Finnhub company-news, 한국=DART 전자공시
  2) 점수화 SentimentScorer: LLM(Anthropic) 요약→[-1,1], 키 없으면 키워드 폴백
  3) 결과 NewsSentiment(score, confidence, summary) → tilt = score*confidence

키 없으면 자동으로 중립/폴백. forecast.ensemble_drift 가 캡을 걸어 과신 방지.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx

from bot.config import settings

log = logging.getLogger(__name__)


@dataclass
class NewsItem:
    headline: str
    summary: str = ""
    source: str = ""
    ts: str = ""


@dataclass
class NewsSentiment:
    score: float          # [-1,1]
    confidence: float     # [0,1]
    summary: str
    sources: int = 0
    items: list[NewsItem] = field(default_factory=list)


# ---------------- 수집기 ----------------
class NewsProvider(ABC):
    @abstractmethod
    def fetch(self, symbol: str, days: int) -> list[NewsItem]: ...


class FinnhubProvider(NewsProvider):
    """미국 종목 뉴스. FINNHUB_API_KEY 필요(finnhub.io 무료티어)."""
    def fetch(self, symbol: str, days: int) -> list[NewsItem]:
        if not settings.finnhub_api_key:
            return []
        today = datetime.now().date()
        frm = (today - timedelta(days=days)).isoformat()
        try:
            r = httpx.get("https://finnhub.io/api/v1/company-news",
                          params={"symbol": symbol, "from": frm,
                                  "to": today.isoformat(),
                                  "token": settings.finnhub_api_key}, timeout=15)
            r.raise_for_status()
            rows = r.json()
            if isinstance(rows, list):                       # datetime 최신순 보장 후 상위20
                rows = sorted(rows, key=lambda x: x.get("datetime", 0), reverse=True)[:20]
            else:
                rows = []
        except httpx.HTTPError:
            log.warning("finnhub 뉴스 조회 실패: %s", symbol)
            return []
        return [NewsItem(headline=x.get("headline", ""),
                         summary=x.get("summary", ""),
                         source=x.get("source", ""),
                         ts=str(x.get("datetime", ""))) for x in rows]


class DartProvider(NewsProvider):
    """한국 전자공시(DART). DART_API_KEY 필요. (목록 조회 → 보고서명 사용)"""
    def fetch(self, symbol: str, days: int) -> list[NewsItem]:
        if not settings.dart_api_key:
            return []
        # NOTE: DART는 종목코드→고유번호(corp_code) 매핑이 필요. 구현 TODO.
        return []


def _provider(market: str) -> NewsProvider:
    return DartProvider() if market == "KR" else FinnhubProvider()


# ---------------- 점수화 ----------------
_POS = ["beat", "surge", "record", "raise", "increase", "upgrade", "growth",
        "strong", "approval", "buyback", "dividend increase", "outperform"]
_NEG = ["miss", "plunge", "cut", "downgrade", "lawsuit", "probe", "warning",
        "decline", "weak", "halt", "recall", "restriction", "ban", "default"]


def _keyword_score(items: list[NewsItem]) -> tuple[float, float, str]:
    text = " ".join((i.headline + " " + i.summary).lower() for i in items)
    if not text.strip():
        return 0.0, 0.0, "뉴스 없음(중립)"
    pos = sum(text.count(w) for w in _POS)
    neg = sum(text.count(w) for w in _NEG)
    if pos + neg == 0:
        return 0.0, 0.2, "중립 키워드"
    score = (pos - neg) / (pos + neg)
    conf = min(1.0, (pos + neg) / 8)
    return score, conf, f"키워드 +{pos}/-{neg}"


def _llm_score(symbol: str, items: list[NewsItem]) -> tuple[float, float, str] | None:
    if not settings.anthropic_api_key or not items:
        return None
    heads = "\n".join(f"- {i.headline}" for i in items[:15])
    prompt = (f"다음은 {symbol}의 최근 뉴스 헤드라인이다. 주가에 대한 종합 감성을 "
              f"JSON으로만 답하라. score:-1~1, confidence:0~1, summary:한줄.\n{heads}")
    try:
        r = httpx.post("https://api.anthropic.com/v1/messages",
                       headers={"x-api-key": settings.anthropic_api_key,
                                "anthropic-version": "2023-06-01",
                                "content-type": "application/json"},
                       json={"model": settings.sentiment_model, "max_tokens": 200,
                             "messages": [{"role": "user", "content": prompt}]},
                       timeout=30)
        r.raise_for_status()
        txt = r.json()["content"][0]["text"]
        s = txt[txt.find("{"):txt.rfind("}") + 1]
        d = json.loads(s)
        return float(d["score"]), float(d["confidence"]), str(d.get("summary", ""))[:80]
    except (httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError):
        log.warning("LLM 감성 분석 실패 → 키워드 폴백")
        return None


def get_sentiment(symbol: str, market: str = "US") -> NewsSentiment:
    """하이브리드: Groq 관련성 필터 → FinBERT 감성 → 집계. 폴백: 키워드.
    ETF(지수추종 등)는 자동으로 구성종목 룩스루로 감성 산출."""
    from bot.sentiment import finbert_scores, groq_relevant_indices, groq_summary
    from bot.etf import lookthrough, etf_news_sentiment

    # ETF면 구성종목 뉴스로 룩스루 (개별종목은 lookthrough=None → 일반 경로)
    lt = lookthrough(symbol)
    if lt and lt.get("type") == "equity":
        res = etf_news_sentiment(symbol)
        if res:
            score, conf, detail = res
            srcs = sum((d[4] if len(d) > 4 else 0) for d in detail)
            return NewsSentiment(score, conf,
                                 f"룩스루({lt['underlying']}) 구성 {len(detail)}종 가중",
                                 srcs, [])
    if lt and lt.get("type") in ("rates", "commodity"):
        # 거시주도 자산: 가짜 뉴스 신호 안 만듦(score 0). 컨텍스트는 macro 모듈에서.
        return NewsSentiment(0.0, 0.0,
                             f"거시주도({lt['underlying']}): 뉴스 미적용, 추세·금리로 판단",
                             0, [])

    items = _provider(market).fetch(symbol, settings.news_lookback_days)
    if not items:
        return NewsSentiment(0.0, 0.0, "뉴스 미수집(키 없음/없음)", 0, [])

    headlines = [i.headline for i in items]
    # 1) 관련성 필터(Groq) — 키 없으면 전체 사용
    idx = groq_relevant_indices(symbol, headlines)
    # idx=[] (Groq가 '전부 무관' 판정) ≠ None(키없음/실패). []면 관련뉴스 0=중립, None이면 전체 사용
    relevant = ([items[i] for i in idx if 0 <= i < len(items)]
                if idx is not None else items)

    # 2) 감성 점수(FinBERT) — 실패 시 키워드 폴백
    fb = finbert_scores([i.headline for i in relevant])
    if fb:
        signed = [s for _, s in fb]
        score = sum(signed) / len(signed)
        # 신뢰도: 기사 수 + 방향 일치도
        agree = sum(1 for s in signed if (s > 0) == (score > 0)) / len(signed)
        conf = min(1.0, len(signed) / 8) * agree
        engine = "FinBERT" + ("+Groq필터" if idx else "")
    else:
        score, conf, kw = _keyword_score(relevant)
        engine = "키워드(폴백)"

    # 주목할 이슈 있을 때만 요약(없으면 빈값→화면서 숨김). 밋밋한 메타 폴백 제거.
    summary = groq_summary(symbol, [i.headline for i in relevant])
    return NewsSentiment(score, conf, summary, len(relevant), relevant)


def tilt(symbol: str, market: str = "US") -> float:
    s = get_sentiment(symbol, market)
    return s.score * s.confidence
