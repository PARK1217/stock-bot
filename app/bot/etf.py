"""ETF 룩스루(look-through) — ETF를 '구성종목'으로 뚫어 분석.

배경: ETF는 자체 '뉴스'가 거의 없다. 하지만 ETF의 실체는 구성종목이다.
  - 추이: ETF 가격(=NAV)에 이미 구성종목이 반영됨 → 스크리너를 ETF에 직접 써도 됨.
  - 뉴스: ETF엔 없으니, '상위 구성종목들의 뉴스 감성'을 비중가중 합산해 ETF 감성으로 본다.

⚠️ 무료 티어엔 실시간 보유종목 API가 없어(Finnhub 유료), 아래는 '지수 기반 대표 구성'이다.
   지수추종 ETF(나스닥100·S&P500)는 이 근사가 충분히 정확. 비중은 근사치 → 주기적 갱신 권장.
   향후: 발행사 CSV/yfinance로 동적 갱신 가능.
"""
from __future__ import annotations

# (종목, 근사비중) — 상위 구성만. 지수추종 ETF는 같은 지수를 공유.
NASDAQ100_TOP = [("NVDA", .09), ("AAPL", .09), ("MSFT", .08), ("AMZN", .055),
                 ("AVGO", .05), ("META", .05), ("GOOGL", .05), ("TSLA", .035),
                 ("COST", .027), ("NFLX", .025)]
SP500_TOP = [("NVDA", .07), ("AAPL", .07), ("MSFT", .065), ("AMZN", .04),
             ("META", .025), ("AVGO", .025), ("GOOGL", .022), ("TSLA", .018),
             ("BRK.B", .017), ("JPM", .015)]
# SCHD: 배당주 중심(상위는 시기별 변동) — 대표 근사
SCHD_TOP = [("AVGO", .045), ("KO", .04), ("VZ", .04), ("AMGN", .04),
            ("ABBV", .038), ("CVX", .037), ("MRK", .037), ("PEP", .035),
            ("HD", .035), ("PFE", .03)]

# ETF → 룩스루 정의. type: equity(구성종목 뉴스 가능) / rates / commodity
LOOKTHROUGH: dict[str, dict] = {
    "JEPQ": {"type": "equity", "underlying": "Nasdaq-100", "top": NASDAQ100_TOP},
    "QQQI": {"type": "equity", "underlying": "Nasdaq-100", "top": NASDAQ100_TOP},
    "QYLD": {"type": "equity", "underlying": "Nasdaq-100", "top": NASDAQ100_TOP},
    "TQQQ": {"type": "equity", "underlying": "Nasdaq-100(3x)", "top": NASDAQ100_TOP},
    "JEPI": {"type": "equity", "underlying": "S&P500(저변동)", "top": SP500_TOP},
    "SPYI": {"type": "equity", "underlying": "S&P500", "top": SP500_TOP},
    "SCHD": {"type": "equity", "underlying": "배당지수", "top": SCHD_TOP},
    "SGOV": {"type": "rates", "underlying": "초단기 국채"},
    "BOXX": {"type": "rates", "underlying": "박스스프레드(금리)"},
    "GLDM": {"type": "commodity", "underlying": "금"},
}


def lookthrough(symbol: str) -> dict | None:
    return LOOKTHROUGH.get(symbol.upper())


def top_constituents(symbol: str, n: int = 6) -> list[tuple[str, float]]:
    info = lookthrough(symbol)
    if not info or info.get("type") != "equity":
        return []
    return info["top"][:n]


def etf_news_sentiment(symbol: str, n: int = 6):
    """ETF 구성종목 뉴스 감성을 비중가중 합산. (score, confidence, detail)
    rates/commodity/미등록이면 None."""
    from bot.news import get_sentiment
    cons = top_constituents(symbol, n)
    if not cons:
        return None
    num = den = 0.0
    detail = []
    for sym, w in cons:
        s = get_sentiment(sym, "US")
        weight = w * max(s.confidence, 0.05)  # 비중 × 신뢰도
        num += s.score * weight
        den += weight
        detail.append((sym, w, s.score, s.confidence, s.sources))
    if den == 0:
        return None
    score = num / den
    conf = min(1.0, den / sum(w for _, w in cons))  # 가중 신뢰도
    return score, conf, detail
