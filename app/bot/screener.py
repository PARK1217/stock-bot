"""추세 스크리너 — 일봉 기반 모멘텀/추세 점수로 종목 랭킹.

목적: "추이 좋은 종목" 탐색. 자동매매가 아니라 후보를 찾아 보여준다.
순수 함수(외부 의존 없음) → 백테스트/대시보드에서도 재사용 가능.

점수 구성(0~100 정규화 전 raw):
  - 1·3개월 수익률(모멘텀)
  - 추세(종가 > SMA20 > SMA50 > SMA200 정렬도)
  - SMA200 대비 이격(상승추세 강도)
  - 변동성 페널티(고변동 감점)
배당/인컴 종목 특성상 과도한 변동보다 '꾸준한 우상향'에 가점.
"""
from __future__ import annotations

from dataclasses import dataclass


def sma(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def ret(values: list[float], n: int) -> float | None:
    if len(values) <= n or values[-n - 1] == 0:
        return None
    return (values[-1] / values[-n - 1] - 1) * 100


def volatility(closes: list[float], n: int = 20) -> float:
    """최근 n일 일간수익률 표준편차(%) 근사."""
    if len(closes) < n + 1:
        return 0.0
    rets = [(closes[i] / closes[i - 1] - 1)
            for i in range(len(closes) - n, len(closes)) if closes[i - 1]]
    if not rets:
        return 0.0
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / len(rets)
    return (var ** 0.5) * 100


@dataclass
class ScreenResult:
    symbol: str
    last: float
    ret_1m: float | None
    ret_3m: float | None
    above_sma200: float | None   # SMA200 대비 이격(%)
    trend_aligned: bool          # 종가>SMA20>SMA50>SMA200
    vol_20d: float
    score: float

    def line(self) -> str:
        def f(x):
            return f"{x:+.1f}%" if x is not None else "  n/a"
        flag = "▲정배열" if self.trend_aligned else "      "
        return (f"{self.symbol:<7} {self.score:6.1f}  {flag}  "
                f"1M {f(self.ret_1m)}  3M {f(self.ret_3m)}  "
                f"vsSMA200 {f(self.above_sma200)}  변동 {self.vol_20d:4.1f}%  "
                f"@{self.last:,.2f}")


def score_symbol(symbol: str, candles: list[dict]) -> ScreenResult | None:
    closes = [c["close"] for c in candles if c["close"] > 0]
    if len(closes) < 30:
        return None
    last = closes[-1]
    r1, r3 = ret(closes, 21), ret(closes, 63)
    s20, s50, s200 = sma(closes, 20), sma(closes, 50), sma(closes, 200)
    vol = volatility(closes, 20)
    above200 = (last / s200 - 1) * 100 if s200 else None
    aligned = bool(s20 and s50 and s200 and last > s20 > s50 > s200)

    # raw score: 모멘텀 + 추세정렬 + 이격 - 변동성
    score = 0.0
    score += (r1 or 0) * 0.5
    score += (r3 or 0) * 0.8
    score += (above200 or 0) * 0.4
    if aligned:
        score += 15
    score -= vol * 1.5
    return ScreenResult(symbol, last, r1, r3, above200, aligned, vol, score)


def screen(candles_by_symbol: dict[str, list[dict]]) -> list[ScreenResult]:
    results = [r for sym, cs in candles_by_symbol.items()
               if (r := score_symbol(sym, cs))]
    results.sort(key=lambda r: r.score, reverse=True)
    return results


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """순위상관(Spearman ρ). 점수와 이후수익의 단조 상관도."""
    n = len(xs)
    if n < 5:
        return None

    def rank(a):
        order = sorted(range(n), key=lambda i: a[i])
        r = [0.0] * n
        i = 0
        while i < n:                       # 동순위 평균처리
            j = i
            while j + 1 < n and a[order[j + 1]] == a[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2.0
            i = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    vx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    vy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    return cov / (vx * vy) if vx and vy else None


def backtest_score(candles_by_symbol: dict[str, list[dict]],
                   horizon: int = 21, min_hist: int = 60) -> dict:
    """점수 신뢰도 백테스트. 과거 각 시점 점수 vs 이후 horizon일 실제수익 대조.
    반환: IC(순위상관)·상하위 스프레드·방향적중률·표본수·등급.
    ※ 가용 캔들(200일) 슬라이딩이라 장기추세 항은 일부만 반영(1차 점검용)."""
    pairs: list[tuple[float, float]] = []  # (score, 이후수익%)
    for sym, cs in candles_by_symbol.items():
        m = len(cs)
        for t in range(min_hist, m - horizon):
            res = score_symbol(sym, cs[:t + 1])
            base = cs[t]["close"]
            fut = cs[t + horizon]["close"]
            if res is None or base <= 0 or fut <= 0:
                continue
            pairs.append((res.score, (fut / base - 1) * 100))

    n = len(pairs)
    if n < 30:
        return {"n": n, "horizon": horizon, "grade": "데이터부족"}

    scores = [p[0] for p in pairs]
    fwds = [p[1] for p in pairs]
    rho = _spearman(scores, fwds)
    ordered = sorted(pairs, key=lambda p: p[0])
    k = n // 3
    high, low = ordered[-k:], ordered[:k]
    high_avg = sum(p[1] for p in high) / k
    low_avg = sum(p[1] for p in low) / k
    spread = high_avg - low_avg
    med = sorted(scores)[n // 2]
    top = [f for s, f in pairs if s >= med]
    hit = sum(1 for f in top if f > 0) / len(top) if top else None

    if rho is None:
        grade = "낮음"
    elif rho < -0.03:
        grade = "역상관"
    elif abs(rho) < 0.05:
        grade = "낮음"
    elif rho < 0.15:
        grade = "보통"
    else:
        grade = "양호"

    return {"n": n, "horizon": horizon, "ic": round(rho, 3) if rho is not None else None,
            "spread": round(spread, 2), "high_avg": round(high_avg, 2),
            "low_avg": round(low_avg, 2),
            "hit": round(hit * 100, 1) if hit is not None else None, "grade": grade}


# 기본 워치리스트 — 미국 배당/인컴 ETF + 사용자 보유군 + 대표 배당성장.
DEFAULT_WATCHLIST = [
    "SCHD", "JEPI", "JEPQ", "QQQI", "SPYI", "QYLD", "DIVO", "O", "VYM",
    "SGOV", "BOXX", "GLDM", "VIG", "DGRO", "HDV", "SPHD", "NVDA", "AAPL",
]
