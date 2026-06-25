"""거시 지표 — 금리/원자재 주도 자산(SGOV·BOXX·GLDM 등)의 환경 컨텍스트.

원칙: 현금성·원자재 ETF에 가짜 '뉴스 호재'를 붙이지 않는다(가격이 거의 안 움직임).
대신 금리 방향(장기채 ETF TLT 추세) 같은 '의사결정 컨텍스트'를 제공한다.
순수 함수 — 종가 시계열을 받아 판단.
"""
from __future__ import annotations

RATE_PROXY = "TLT"  # 20년+ 국채 ETF. 가격↓ = 시장금리↑ (역의 관계)


def _momentum(closes: list[float], n: int) -> float | None:
    if len(closes) <= n or closes[-n - 1] == 0:
        return None
    return closes[-1] / closes[-n - 1] - 1


def rates_context(tlt_closes: list[float]) -> tuple[str, str]:
    """장기채(TLT) 추세로 금리 방향 판단. 반환 (방향, 설명)."""
    m = _momentum(tlt_closes, 21)
    if m is None:
        return "unknown", "금리 데이터 부족"
    if m <= -0.015:   # TLT 하락 = 금리 상승
        return "rising", (f"금리 상승 국면 (TLT 1M {m*100:+.1f}%) — "
                          "현금성(SGOV/BOXX) 수익률↑, 듀레이션·금 역풍")
    if m >= 0.015:
        return "falling", (f"금리 하락 국면 (TLT 1M {m*100:+.1f}%) — "
                           "채권·금 우호, 현금성 매력↓")
    return "flat", f"금리 횡보 (TLT 1M {m*100:+.1f}%)"


def gold_context(gldm_closes: list[float]) -> str:
    m = _momentum(gldm_closes, 21)
    if m is None:
        return "금 데이터 부족"
    return (f"금 추세 1M {m*100:+.1f}% — 가격=금시세 직접반영, "
            "거시(실질금리·달러)에 좌우")


def asset_context(symbol: str, closes: list[float],
                  rate_proxy_closes: list[float] | None = None) -> str:
    """거시주도 자산의 한 줄 컨텍스트."""
    sym = symbol.upper()
    if sym in ("SGOV", "BOXX"):
        if rate_proxy_closes:
            return "현금성 — " + rates_context(rate_proxy_closes)[1]
        return "현금성 — 금리 환경에 좌우(데이터 필요)"
    if sym == "GLDM":
        return gold_context(closes)
    return ""
