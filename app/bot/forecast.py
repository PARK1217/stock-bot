"""예측 엔진 — 정직한 확률 기반 가격 전망 (앙상블).

단정("X% 확률로 +8%")이 아니라, 여러 근거를 투명하게 합산한 '확률 분포'를 낸다.
구성 요소(각 기여도를 결과에 함께 노출):
  1) 통계적 몬테카를로: 과거 일간 로그수익률의 드리프트(μ)·변동성(σ)로 미래 경로 시뮬레이션
     → 목표 도달 확률(경로 중 한 번이라도 터치), 종가 분포 밴드, 기대 고점/저점
  2) 백테스트 적중률: 동일 신호가 과거 H일 내 상승으로 이어진 실측 비율(표본수 포함)
  3) 앙상블 드리프트 조정: 기술적 추세 + 뉴스/공시 감성으로 μ를 '소폭' 보정(과신 방지 캡)

주의: 모든 확률은 '모델 추정치'. 실제 신뢰도는 백테스트 적중률 + 자기예측추적으로만 검증된다.
순수 함수(표준 라이브러리만) → API/대시보드/백테스트에서 재사용.
"""
from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field


def log_returns(closes: list[float]) -> list[float]:
    out = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            out.append(math.log(closes[i] / closes[i - 1]))
    return out


def _shrink(mu: float, factor: float = 0.4) -> float:
    """과거 드리프트는 노이즈가 크다 → 0쪽으로 수축(과적합 방지)."""
    return mu * factor


@dataclass
class Forecast:
    symbol: str
    last: float
    horizon_days: int
    prob_up: float                 # 종가 기준 상승 확률
    exp_return: float              # 기대 수익률(%)
    band: dict[str, float]         # 종가 분위수 {p10,p25,p50,p75,p90}
    target_touch: dict[str, float] # {+5%: 확률, +10%: ...} 경로 터치 확률
    down_touch: dict[str, float]   # {-5%: 확률, ...}
    exp_peak_pct: float            # 기대 고점(경로 max 중앙값, %)
    exp_trough_pct: float          # 기대 저점(%)
    backtest_winrate: float | None # 동일신호 과거 적중률
    backtest_n: int                # 표본수
    drift_parts: dict[str, float] = field(default_factory=dict)  # 기여도 투명공개
    daily_vol_pct: float = 0.0

    def report(self) -> str:
        def pct(x):
            return f"{x*100:5.1f}%"
        lines = [
            f"[{self.symbol}] 현재 {self.last:,.2f}  / {self.horizon_days}일 전망",
            f"  상승확률(종가) {pct(self.prob_up)}   기대수익 {self.exp_return:+.1f}%   "
            f"일변동성 {self.daily_vol_pct:.1f}%",
            f"  종가밴드  P10 {self.band['p10']:,.2f} | P25 {self.band['p25']:,.2f} | "
            f"P50 {self.band['p50']:,.2f} | P75 {self.band['p75']:,.2f} | P90 {self.band['p90']:,.2f}",
            "  목표 도달확률(기간내 터치): " + "  ".join(
                f"{k} {pct(v)}" for k, v in self.target_touch.items()),
            "  하락 터치확률: " + "  ".join(
                f"{k} {pct(v)}" for k, v in self.down_touch.items()),
            f"  기대 고점 {self.exp_peak_pct:+.1f}%  / 기대 저점 {self.exp_trough_pct:+.1f}%",
        ]
        if self.backtest_winrate is not None:
            lines.append(f"  ※ 동일신호 과거 적중률 {self.backtest_winrate*100:.0f}% "
                         f"(표본 {self.backtest_n}건) — 실측 검증치")
        lines.append("  드리프트 기여: " + ", ".join(
            f"{k} {v*100:+.2f}%" for k, v in self.drift_parts.items()))
        lines.append("  ⚠️ 모델 추정치 — 단정 아님. 결정은 사용자.")
        return "\n".join(lines)


def backtest_directional(closes: list[float], horizon: int,
                         signal_fn) -> tuple[float | None, int]:
    """signal_fn(prefix_closes)->bool 가 True인 날들에서 H일 뒤 상승했는지 적중률."""
    wins = total = 0
    for i in range(60, len(closes) - horizon):
        if signal_fn(closes[:i + 1]):
            total += 1
            if closes[i + horizon] > closes[i]:
                wins += 1
    if total == 0:
        return None, 0
    return wins / total, total


def default_signal(closes: list[float]) -> bool:
    """기본 신호: 종가>SMA20>SMA50 (정배열 추세)."""
    if len(closes) < 50:
        return False
    s20 = sum(closes[-20:]) / 20
    s50 = sum(closes[-50:]) / 50
    return closes[-1] > s20 > s50


def ensemble_drift(hist_mu: float, technical_tilt: float = 0.0,
                   news_sentiment: float = 0.0, *,
                   w_tech: float = 0.0008, w_news: float = 0.0010,
                   cap: float = 0.002) -> tuple[float, dict]:
    """일간 드리프트 = 수축된 과거μ + 기술적틸트 + 뉴스감성. 과신 방지 위해 캡.
    technical_tilt, news_sentiment 은 [-1,1] 정규화 입력."""
    base = _shrink(hist_mu)
    tech = max(-1, min(1, technical_tilt)) * w_tech
    news = max(-1, min(1, news_sentiment)) * w_news
    drift = base + tech + news
    drift = max(-cap, min(cap, drift))  # 일간 ±0.2% 캡
    parts = {"과거추세(수축)": base, "기술적": tech, "뉴스공시": news}
    return drift, parts


def monte_carlo(closes: list[float], horizon: int = 21,
                n_paths: int = 10000, technical_tilt: float = 0.0,
                news_sentiment: float = 0.0, symbol: str = "",
                seed: int | None = 7) -> Forecast | None:
    rets = log_returns(closes)
    if len(rets) < 30:
        return None
    rng = random.Random(seed)
    mu = statistics.fmean(rets)
    sigma = statistics.pstdev(rets) or 1e-6
    drift, parts = ensemble_drift(mu, technical_tilt, news_sentiment)
    S0 = closes[-1]

    terminals, peaks, troughs = [], [], []
    targets = [0.03, 0.05, 0.10]
    downs = [0.03, 0.05, 0.10]
    touch_up = {t: 0 for t in targets}
    touch_dn = {d: 0 for d in downs}

    for _ in range(n_paths):
        s = S0
        hi = lo = S0
        for _ in range(horizon):
            s *= math.exp(drift + sigma * rng.gauss(0, 1))
            hi = max(hi, s)
            lo = min(lo, s)
        terminals.append(s)
        peaks.append(hi / S0 - 1)
        troughs.append(lo / S0 - 1)
        for t in targets:
            if hi >= S0 * (1 + t):
                touch_up[t] += 1
        for d in downs:
            if lo <= S0 * (1 - d):
                touch_dn[d] += 1

    terminals.sort()

    def q(p):
        return terminals[min(len(terminals) - 1, int(p * len(terminals)))]

    prob_up = sum(1 for x in terminals if x > S0) / n_paths
    exp_return = (statistics.fmean(terminals) / S0 - 1) * 100
    return Forecast(
        symbol=symbol, last=S0, horizon_days=horizon,
        prob_up=prob_up, exp_return=exp_return,
        band={"p10": q(.10), "p25": q(.25), "p50": q(.50),
              "p75": q(.75), "p90": q(.90)},
        target_touch={f"+{int(t*100)}%": touch_up[t] / n_paths for t in targets},
        down_touch={f"-{int(d*100)}%": touch_dn[d] / n_paths for d in downs},
        exp_peak_pct=statistics.median(peaks) * 100,
        exp_trough_pct=statistics.median(troughs) * 100,
        backtest_winrate=None, backtest_n=0,
        drift_parts=parts, daily_vol_pct=sigma * 100,
    )


def forecast_symbol(symbol: str, closes: list[float], horizon: int = 21,
                    technical_tilt: float = 0.0, news_sentiment: float = 0.0
                    ) -> Forecast | None:
    """엔드투엔드: 몬테카를로 + 동일신호 백테스트 적중률 결합."""
    fc = monte_carlo(closes, horizon, technical_tilt=technical_tilt,
                     news_sentiment=news_sentiment, symbol=symbol)
    if fc is None:
        return None
    wr, n = backtest_directional(closes, horizon, default_signal)
    fc.backtest_winrate, fc.backtest_n = wr, n
    return fc
