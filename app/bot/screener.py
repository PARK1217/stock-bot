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


def rel_volume(candles: list[dict], n: int = 20) -> float | None:
    """상대거래량(RVOL) = 최근일 거래량 / 직전 n일 평균거래량.
    1.0=평소, 1.5↑ 활발, 2.0↑ 급증. 거래량은 '움직임의 진위'를 확인해준다. 데이터부족=None."""
    vols = [c.get("volume", 0) for c in candles if c.get("volume", 0) > 0]
    if len(vols) < 6:
        return None
    base = vols[-(n + 1):-1] if len(vols) > n else vols[:-1]
    avg = sum(base) / len(base) if base else 0.0
    return round(vols[-1] / avg, 2) if avg > 0 else None


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
    rvol: float | None = None    # 상대거래량(오늘/평균)

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
    # 거래량 확인: 상승을 거래량이 동반하면 신호 신뢰↑(가산), 상승인데 거래량 빈약하면 약한신호
    rv = rel_volume(candles)
    if rv is not None and (r1 or 0) > 0:
        score += min(max(rv - 1.0, 0.0), 1.5) * 4   # RVOL 1→0, 2.5↑→최대 +6
    return ScreenResult(symbol, last, r1, r3, above200, aligned, vol, score, rv)


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


def backtest_trend(candles: list[dict], ma: int = 20, fee: float = 0.001) -> dict:
    """추세추종 타이밍 백테스트. 전일 종가>이평(ma)이면 보유, 아니면 현금.
    추세추종 vs 그냥보유 — 수익·최대낙폭(MDD) 비교. (룩어헤드 방지: 전일 신호)"""
    cl = [c["close"] for c in candles if c["close"] > 0]
    if len(cl) < ma + 5:
        return {}
    pos = 0
    eq = peak = 1.0
    mdd = 0.0
    bh = bh_peak = 1.0
    bh_mdd = 0.0
    sw = 0
    for i in range(ma, len(cl)):
        sma = sum(cl[i - ma:i]) / ma
        sig = 1 if cl[i - 1] > sma else 0               # 전일 종가 기준
        r = cl[i] / cl[i - 1] - 1
        eq *= (1 + sig * r)
        if sig != pos:
            eq *= (1 - fee); sw += 1                     # 진입/청산 수수료
        pos = sig
        peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
        bh *= (1 + r); bh_peak = max(bh_peak, bh); bh_mdd = max(bh_mdd, (bh_peak - bh) / bh_peak)
    return {"ma": ma, "trend_ret": round((eq - 1) * 100, 1), "trend_mdd": round(mdd * 100, 1),
            "bh_ret": round((bh - 1) * 100, 1), "bh_mdd": round(bh_mdd * 100, 1), "switches": sw}


def backtest_breakout(candles: list[dict], k: float = 0.5, fee: float = 0.001) -> dict:
    """변동성 돌파(데이트레이딩) 백테스트. 일봉 OHLC로 시뮬.
    목표가=당일시가+k×전일(고-저). 당일고가≥목표가면 목표가 진입→종가 청산.
    fee=왕복 수수료+세금 비율. buy&hold와 비교."""
    rets, days = [], 0
    for i in range(1, len(candles)):
        p, c = candles[i - 1], candles[i]
        rng = p["high"] - p["low"]
        if rng <= 0 or c["open"] <= 0:
            continue
        days += 1
        target = c["open"] + k * rng
        if c["high"] >= target and target > 0:           # 돌파→진입
            rets.append(c["close"] / target - 1 - fee)   # 종가청산 - 수수료
    if not rets:
        return {"trades": 0}
    cum = 1.0
    for r in rets:
        cum *= (1 + r)
    wins = sum(1 for r in rets if r > 0)
    bh = (candles[-1]["close"] / candles[1]["open"] - 1) * 100 if len(candles) > 1 else 0
    return {"days": days, "trades": len(rets),
            "win_pct": round(wins / len(rets) * 100, 1),
            "avg_ret": round(sum(rets) / len(rets) * 100, 3),
            "cum_ret": round((cum - 1) * 100, 1),
            "buyhold": round(bh, 1)}


def backtest_strategy(candles_by_symbol: dict[str, list[dict]],
                      k: int = 3, horizon: int = 21, min_hist: int = 60,
                      min_bars: int = 0) -> dict:
    """전략 백테스트. 매 리밸런싱(horizon일)마다 점수 상위k(모멘텀)·하위k(평균회귀)·
    전체(벤치)를 동일가중 보유 → 다음 구간 수익. 누적·평균 수익 비교.
    min_bars: 이 봉수 미만 이력 종목은 제외(짧은 종목이 align-to-min으로 구간을
    깎는 문제 완화 — minlen↑→구간수↑)."""
    closes = {s: [c["close"] for c in cs if c["close"] > 0]
              for s, cs in candles_by_symbol.items()}
    floor = max(min_hist + horizon + 5, min_bars)
    closes = {s: v for s, v in closes.items() if len(v) >= floor}
    if len(closes) < 2 * k:
        return {"error": "종목/데이터 부족", "included": len(closes)}
    minlen = min(len(v) for v in closes.values())
    al = {s: v[-minlen:] for s, v in closes.items()}        # 끝 기준 정렬
    top, bot, bench = [], [], []
    t = min_hist
    while t + horizon < minlen:
        sc = {}
        for s in al:
            r = score_symbol(s, [{"close": c} for c in al[s][:t + 1]])
            if r:
                sc[s] = r.score
        if len(sc) >= 2 * k:
            ranked = sorted(sc, key=sc.get)
            lo, hi = ranked[:k], ranked[-k:]
            def rr(sym):
                return al[sym][t + horizon] / al[sym][t] - 1
            top.append(sum(rr(s) for s in hi) / k)
            bot.append(sum(rr(s) for s in lo) / k)
            bench.append(sum(rr(s) for s in sc) / len(sc))
        t += horizon

    def cum(rs):
        c = 1.0
        for r in rs:
            c *= (1 + r)
        return (c - 1) * 100

    n = len(top)
    if not n:
        return {"error": "구간 부족"}

    def tstat(a: list[float], b: list[float]) -> float | None:
        """대응표본 t값(a-b 구간차의 평균/표준오차). |t|>2면 유의."""
        d = [a[i] - b[i] for i in range(len(a))]
        if len(d) < 2:
            return None
        m = sum(d) / len(d)
        var = sum((x - m) ** 2 for x in d) / (len(d) - 1)
        se = (var / len(d)) ** 0.5
        return round(m / se, 2) if se else None

    return {"periods": n, "horizon": horizon, "k": k,
            "included": len(closes), "minlen": minlen,
            "momentum_cum": round(cum(top), 1), "reversion_cum": round(cum(bot), 1),
            "bench_cum": round(cum(bench), 1),
            "momentum_avg": round(sum(top) / n * 100, 2),
            "reversion_avg": round(sum(bot) / n * 100, 2),
            "bench_avg": round(sum(bench) / n * 100, 2),
            # 유의성: 구간수 적으면 |t|<2로 "엣지 아님"이 정상(노이즈)
            "t_mom_vs_rev": tstat(top, bot), "t_mom_vs_bench": tstat(top, bench),
            "t_rev_vs_bench": tstat(bot, bench)}


def _max_drawdown(equity: list[float]) -> float:
    """최대낙폭(%) — 음수 반환."""
    peak, mdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    return round(mdd * 100, 1)


def _sharpe(daily_rets: list[float]) -> float | None:
    """연율화 샤프(무위험 0 가정, 252일)."""
    n = len(daily_rets)
    if n < 2:
        return None
    m = sum(daily_rets) / n
    var = sum((r - m) ** 2 for r in daily_rets) / (n - 1)
    sd = var ** 0.5
    return round(m / sd * (252 ** 0.5), 2) if sd else None


def _metrics(equity: list[float]) -> dict:
    rets = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity)) if equity[i - 1]]
    return {"total": round((equity[-1] / equity[0] - 1) * 100, 1) if equity[0] else None,
            "mdd": _max_drawdown(equity), "sharpe": _sharpe(rets)}


def backtest_portfolio(candles_by_symbol: dict[str, list[dict]],
                       core_syms: list[str], sat_syms: list[str],
                       bench: tuple[str, ...] = ("SPY", "SCHD"),
                       rebal: int = 5, ma: int = 50, core_w: float = 0.70,
                       core_n: int = 4, sat_n: int = 2, fee: float = 0.001,
                       use_ma: bool = True, use_regime: bool = True) -> dict:
    """실제 배포 전략(MA50 추세필터+코어/새틀+SPY레짐 방어)을 포트폴리오 단위로 시뮬.
    rebal일마다 리밸런싱, 주식가치 share기반 추적, 회전수수료 차감. SPY/SCHD buy&hold와
    총수익·최대낙폭(MDD)·샤프 비교. ※통화혼합 방지 위해 US 슬리브(+벤치)만 평가.
    SPY 캔들이 candles_by_symbol에 있어야 레짐 판정 가능."""
    ser = {s: [c["close"] for c in cs if c["close"] > 0]
           for s, cs in candles_by_symbol.items()}
    need = set(core_syms) | set(sat_syms) | set(bench) | {"SPY"}
    ser = {s: v for s, v in ser.items() if s in need and len(v) >= ma + rebal + 5}
    if "SPY" not in ser:
        return {"error": "SPY 캔들 없음(레짐 판정 불가)"}
    minlen = min(len(v) for v in ser.values())
    al = {s: v[-minlen:] for s, v in ser.items()}
    spy = al["SPY"]
    core_syms = [s for s in core_syms if s in al]
    sat_syms = [s for s in sat_syms if s in al]
    start = ma
    if minlen <= start + rebal:
        return {"error": "데이터 부족", "minlen": minlen}

    def trend_ok(s, t):
        return True if not use_ma else al[s][t] > sum(al[s][t - ma:t]) / ma

    def score(s, t):
        r = score_symbol(s, [{"close": c} for c in al[s][:t + 1]])
        return r.score if r else None

    units: dict[str, float] = {}
    cash = 1.0
    eq: list[float] = []
    for t in range(start, minlen):
        pv = cash + sum(u * al[s][t] for s, u in units.items())
        eq.append(pv)
        if (t - start) % rebal:
            continue
        regime_on = True if not use_regime else spy[t] > sum(spy[t - ma:t]) / ma
        core_c = sorted(((s, sc) for s in core_syms if trend_ok(s, t)
                         and (sc := score(s, t)) is not None),
                        key=lambda x: -x[1])[:core_n]
        sat_c = []
        if regime_on:
            sat_c = sorted(((s, sc) for s in sat_syms if trend_ok(s, t)
                            and (sc := score(s, t)) is not None),
                           key=lambda x: -x[1])[:sat_n]
        w: dict[str, float] = {}
        if regime_on:
            for s, _ in core_c:
                w[s] = core_w / core_n
            for s, _ in sat_c:
                w[s] = (1 - core_w) / sat_n
        else:                                       # risk_off: 코어 50%만(절반 현금)
            for s, _ in core_c:
                w[s] = 0.5 / core_n
        old_val = {s: units.get(s, 0) * al[s][t] for s in set(units) | set(w)}
        new_val = {s: pv * w.get(s, 0.0) for s in set(units) | set(w)}
        turnover = sum(abs(new_val[s] - old_val.get(s, 0)) for s in new_val)
        pv -= fee * turnover
        units = {s: pv * w[s] / al[s][t] for s in w if w[s] > 0}
        cash = pv - sum(u * al[s][t] for s, u in units.items())

    out = {"periods": len(eq), "rebal": rebal, "ma": ma, "minlen": minlen,
           "strategy": _metrics(eq)}
    for b in bench:
        if b in al:
            out[b] = _metrics([al[b][t] for t in range(start, minlen)])
    return out


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

# KR 워치리스트 — 한투 보유 + 모의 KR 유니버스(국내상장 ETF). KIS 캔들.
KR_WATCHLIST = [
    "069500", "133690", "360750", "379800", "458730", "161510", "329200",
    "210780", "484790", "273130", "122630", "233740",
]

# 단일종목(개별주) 워치리스트 — ETF와 분리해 추세 스크리닝(변동 커서 검증 병행).
SINGLE_US = [
    "NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AVGO",
    "AMD", "NFLX", "COST", "LLY", "V", "JPM", "PLTR",
]
SINGLE_KR = [
    "005930", "000660", "373220", "207940", "005380", "000270",   # 삼성전자·SK하이닉스·LG엔솔·삼바·현대차·기아
    "035420", "035720", "005490", "068270", "105560", "012330",   # NAVER·카카오·POSCO홀딩스·셀트리온·KB금융·현대모비스
]
