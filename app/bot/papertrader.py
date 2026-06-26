"""모의계좌 자동매매 — '코어-새틀라이트 + 추세추종' 전략을 KIS 모의(가상 5억)에 실행.

설계 근거(백테스트 검증):
- 종목선택/단타의 엣지는 작음 → 분산 보유가 베이스. (코어 70%)
- 모멘텀엔 약한 +엣지 → 추세 강한 종목에 가중. (새틀라이트 30%)
- MA50 추세필터는 수익은 약간 깎아도 최대낙폭(MDD)을 크게 줄임 → '추세 꺾이면 현금화'로
  하락장 방어. 진입/청산 타이밍의 핵심.

운용: KR=KIS 캔들, US=토스 캔들. 실행=KIS 모의(국내 시장가 / 해외 마켓터블 지정가).
각 시장 열렸을 때만 체결(KR 09:00-15:30, US 23:30-06:00 KST). 통합증거금 1계좌(5억).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

import redis

from bot.brokers.base import Side
from bot.brokers.kis import KISBroker
from bot.brokers.toss import TossBroker
from bot.config import settings
from bot.forecast import forecast_symbol
from bot.screener import score_symbol
from bot.storage.db import SessionLocal
from bot.storage.models import OrderLog, PaperSnapshot

log = logging.getLogger(__name__)
_redis = redis.from_url(settings.redis_url)
NEWS_BLOCK = -0.25            # 뉴스 틸트 이 값 미만이면 강한 악재 → 후보 제외


def _news_tilt(sym: str, market: str) -> float:
    """종목 뉴스 감성 틸트(score×confidence, -1~1). 4h redis 캐시."""
    key = f"paper:news:{sym}"
    c = _redis.get(key)
    if c is not None:
        try:
            return float(c)
        except (TypeError, ValueError):
            pass
    tilt = 0.0
    try:
        from bot.news import get_sentiment
        ns = get_sentiment(sym, market)
        tilt = max(-1.0, min(1.0, ns.score * ns.confidence))
    except Exception:  # noqa: BLE001
        tilt = 0.0
    _redis.set(key, tilt, ex=14400)
    return tilt

# 코어 = 우량 지수·배당 ETF(안정). 새틀라이트 = 고변동(추세 탈 때만).
CORE_KR = ["458730", "360750", "133690", "069500", "379800", "161510", "329200"]
CORE_US = ["SCHD", "JEPQ", "SPYI", "JEPI", "VIG", "DGRO", "O", "QQQI"]
SAT_KR = ["122630", "233740"]                  # KODEX 레버리지·코스닥150레버리지
SAT_US = ["SOXL", "TQQQ", "NVDA"]              # 고변동 성장/레버리지
CORE_N, SAT_N = 4, 2                            # 코어 4슬롯 / 새틀 2슬롯
CORE_W = 0.70                                   # 코어 70% / 새틀 30%
TREND_MA = 50                                   # 추세필터 이동평균(검증: MA50이 낙폭 방어)


def _trend_ok(closes: list[float]) -> bool:
    """MA50 추세 필터 — 종가가 50일 이동평균 위(상승추세)일 때만 True."""
    if len(closes) < TREND_MA + 1:
        return False
    return closes[-1] > sum(closes[-TREND_MA:]) / TREND_MA


def _open_market() -> str | None:
    """현재 열린 시장(KST): KR(09:00-15:30) / US(23:30-06:00) / None(마감)."""
    n = datetime.now()
    t = n.hour * 60 + n.minute
    if 9 * 60 <= t <= 15 * 60 + 30:
        return "KR"
    if t >= 23 * 60 + 30 or t <= 6 * 60:
        return "US"
    return None


def _combined_score(sym: str, closes: list[float], news_tilt: float = 0.0):
    """스크리너 점수 + 예측 상승확률 + 뉴스 감성 종합."""
    sc = score_symbol(sym, [{"close": c} for c in closes])
    if not sc:
        return None
    fc = forecast_symbol(sym, closes, 21,
                         technical_tilt=max(-1, min(1, sc.score / 25)),
                         news_sentiment=news_tilt)
    prob = fc.prob_up if fc else 0.5
    return sc.score + (prob - 0.5) * 100 + news_tilt * 15     # 뉴스 가중


def _rank_pool(pool, market, get_candles) -> list[dict]:
    """추세 in(MA50 위) + 강한 악재 아닌 종목만 후보로, 종합점수순 정렬."""
    out = []
    for sym in pool:
        try:
            closes = [c["close"] for c in get_candles(sym, "1d", 200) if c["close"] > 0]
        except Exception:  # noqa: BLE001
            continue
        if len(closes) < TREND_MA + 5 or not _trend_ok(closes):   # 추세 필터
            continue
        tilt = _news_tilt(sym, market)
        if tilt < NEWS_BLOCK:                                     # 강한 악재 → 제외
            log.info("뉴스 악재로 제외: %s tilt=%.2f", sym, tilt)
            continue
        s = _combined_score(sym, closes, tilt)
        if s is not None:
            out.append({"symbol": sym, "market": market, "score": s, "price": closes[-1]})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def _market_regime(toss) -> str:
    """시장 대표(SPY) MA50 추세 — 'risk_on'(위)/'risk_off'(아래). 하락장 방어용."""
    try:
        closes = [c["close"] for c in toss.get_candles("SPY", "1d", 200) if c["close"] > 0]
        if len(closes) >= TREND_MA + 1:
            return "risk_on" if closes[-1] > sum(closes[-TREND_MA:]) / TREND_MA else "risk_off"
    except Exception:  # noqa: BLE001
        pass
    return "risk_on"


def _select(kis, toss) -> tuple[list[dict], list[dict]]:
    """코어/새틀라이트 각각 추세 통과 상위 N종 선정."""
    core = _rank_pool(CORE_KR, "KR", kis.get_candles) + _rank_pool(CORE_US, "US", toss.get_candles)
    core.sort(key=lambda x: x["score"], reverse=True)
    sat = _rank_pool(SAT_KR, "KR", kis.get_candles) + _rank_pool(SAT_US, "US", toss.get_candles)
    sat.sort(key=lambda x: x["score"], reverse=True)
    return core[:CORE_N], sat[:SAT_N]


def run_paper(dry: bool = False) -> dict:
    kis = KISBroker(paper=True)   # 항상 모의
    toss = TossBroker()
    fx = toss.usdkrw() or 1540.0

    core_t, sat_t = _select(kis, toss)
    regime = _market_regime(toss)
    if regime == "risk_off":                 # 시장 하락추세 → 공격 중단, 방어
        sat_t = []
    targets = core_t + sat_t
    if not targets:
        log.info("추세 통과 종목 없음/방어 — 매수 보류")
    tsyms = {t["symbol"] for t in targets}
    sat_syms = {t["symbol"] for t in sat_t}

    kbal = kis.get_balance()                 # KR 보유 + KRW
    obal = kis.get_overseas_balance()        # US 보유
    held = {p.symbol: (p, "KR") for p in kbal.positions}
    held.update({p.symbol: (p, "US") for p in obal.positions})
    # 통합증거금이라 단순합산은 이중계산 → 초기 5억 + 보유 손익으로 일관 계산
    kr_pnl = sum((p.current_price - p.avg_price) * p.qty for p in kbal.positions)
    us_pnl = sum((p.current_price - p.avg_price) * p.qty for p in obal.positions) * fx
    total_krw = 500_000_000 + kr_pnl + us_pnl
    if regime == "risk_off":                         # 하락장: 코어 50%만(절반 현금)
        core_per, sat_per = total_krw * 0.5 / CORE_N, 0.0
    else:
        core_per = total_krw * CORE_W / CORE_N
        sat_per = total_krw * (1 - CORE_W) / SAT_N
    per_of = {t["symbol"]: (sat_per if t["symbol"] in sat_syms else core_per) for t in targets}
    market = _open_market()

    orders = []  # (symbol, side, qty, market, position)
    # ① 타겟外(추세 이탈 포함) 보유 전량 매도 — 열린 시장만
    for sym, (p, mk) in held.items():
        if sym not in tsyms and p.qty > 0 and mk == market:
            orders.append((sym, Side.SELL, int(p.qty), mk, p))
    # ② 타겟 매수 — 슬롯 예산(per)까지, 열린 시장만
    for t in targets:
        if t["market"] != market:
            continue
        f = fx if t["market"] == "US" else 1.0
        cur_krw = held[t["symbol"]][0].market_value * f if t["symbol"] in held else 0
        qty = int((per_of[t["symbol"]] - cur_krw) // (t["price"] * f))
        if qty > 0:
            orders.append((t["symbol"], Side.BUY, qty, t["market"], None))

    executed = []
    with SessionLocal() as session:
        for sym, side, qty, mk, p in orders:
            if dry:
                executed.append(f"[DRY] {mk} {side.value} {sym} {qty}")
                continue
            if mk == "KR":
                res = kis.place_order(sym, side, qty)            # 국내 시장가
            else:
                base = next((t["price"] for t in targets if t["symbol"] == sym),
                            p.current_price if p else 0)
                lim = base * (1.01 if side == Side.BUY else 0.99)  # 마켓터블 지정가
                res = kis.place_overseas_order(sym, side, qty, lim)
            time.sleep(0.4)
            executed.append(f"{'✅' if res.ok else '❌'} {mk} {side.value} {sym} {qty}: {res.message}")
            session.add(OrderLog(broker=f"kis-paper-{mk.lower()}", mode="paper",
                                 symbol=sym, side=side.value, qty=qty, ok=res.ok,
                                 order_id=res.order_id, message=(res.message or "")[:250]))
        if not dry:
            session.add(PaperSnapshot(cash=kbal.cash, total_eval=total_krw,
                                      holdings=len(held)))
        session.commit()

    result = {"total": total_krw, "market": market or "마감", "regime": regime,
              "core": [f"{t['symbol']}({t['market']})" for t in core_t],
              "sat": [f"{t['symbol']}({t['market']})" for t in sat_t],
              "targets": [f"{t['symbol']}({t['market']})" for t in targets],
              "orders": executed}
    # 대시보드용 전략 현황 저장(코어/새틀 심볼, 슬롯 예산, 시장레짐)
    try:
        import json as _json
        _redis.set("paper:strategy", _json.dumps({
            "core": [t["symbol"] for t in core_t],
            "sat": [t["symbol"] for t in sat_t],
            "core_per": round(core_per), "sat_per": round(sat_per), "regime": regime,
            "ts": str(datetime.now())[:16]}), ex=172800)
    except Exception:  # noqa: BLE001
        pass
    return result
