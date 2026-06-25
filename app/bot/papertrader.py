"""모의계좌 자동매매 — KR+US 통합 '예측+스크리너 종합' 전략을 KIS 모의(가상 5억)에 실행.

목적: 우리가 설계한 전략을 실돈 위험 0으로 검증. 통합증거금이라 한 계좌(5억)가
KR·US를 함께 백업 → KR+US 유니버스를 함께 랭킹해 상위 N종을 한 포트폴리오로 운용.
각 시장이 열렸을 때 해당 종목만 체결(KR 09:00-15:30, US 23:30-06:00 KST).

데이터: KR=KIS 캔들, US=토스 캔들(KIS 해외시세는 모의 제한). 실행=KIS 모의(국내 시장가 / 해외 지정가).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from bot.brokers.base import Side
from bot.brokers.kis import KISBroker
from bot.brokers.toss import TossBroker
from bot.forecast import forecast_symbol
from bot.screener import score_symbol
from bot.storage.db import SessionLocal
from bot.storage.models import OrderLog, PaperSnapshot

log = logging.getLogger(__name__)

KR_UNIVERSE = ["458730", "360750", "133690", "069500", "379800",
               "161510", "329200", "273130"]
US_UNIVERSE = ["SCHD", "JEPQ", "SPYI", "JEPI", "VIG", "DGRO", "O", "QQQI"]
TOP_N = 6


def _open_market() -> str | None:
    """현재 열린 시장(KST): KR(09:00-15:30) / US(23:30-06:00) / None(마감)."""
    n = datetime.now()
    t = n.hour * 60 + n.minute
    if 9 * 60 <= t <= 15 * 60 + 30:
        return "KR"
    if t >= 23 * 60 + 30 or t <= 6 * 60:
        return "US"
    return None


def _combined_score(sym: str, closes: list[float]):
    sc = score_symbol(sym, [{"close": c} for c in closes])
    if not sc:
        return None
    fc = forecast_symbol(sym, closes, 21,
                         technical_tilt=max(-1, min(1, sc.score / 25)),
                         news_sentiment=0)
    prob = fc.prob_up if fc else 0.5
    return sc.score + (prob - 0.5) * 100


def _rank(kis, toss) -> list[dict]:
    ranked = []
    for sym in KR_UNIVERSE:
        try:
            closes = [c["close"] for c in kis.get_candles(sym, "1d", 200) if c["close"] > 0]
        except Exception:  # noqa: BLE001
            continue
        if len(closes) >= 40 and (s := _combined_score(sym, closes)) is not None:
            ranked.append({"symbol": sym, "market": "KR", "score": s, "price": closes[-1]})
    for sym in US_UNIVERSE:
        try:
            closes = [c["close"] for c in toss.get_candles(sym, "1d", 200) if c["close"] > 0]
        except Exception:  # noqa: BLE001
            continue
        if len(closes) >= 40 and (s := _combined_score(sym, closes)) is not None:
            ranked.append({"symbol": sym, "market": "US", "score": s, "price": closes[-1]})
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked


def run_paper(top_n: int = TOP_N, dry: bool = False) -> dict:
    kis = KISBroker(paper=True)   # 항상 모의
    toss = TossBroker()
    fx = toss.usdkrw() or 1540.0

    ranked = _rank(kis, toss)
    if not ranked:
        return {"error": "랭킹 산출 실패"}
    targets = ranked[:top_n]
    tsyms = {t["symbol"] for t in targets}

    kbal = kis.get_balance()                 # KR 보유 + KRW
    obal = kis.get_overseas_balance()        # US 보유
    held = {p.symbol: (p, "KR") for p in kbal.positions}
    held.update({p.symbol: (p, "US") for p in obal.positions})
    # 통합증거금이라 단순합산은 이중계산 → 초기 5억 + 보유 손익으로 일관 계산
    kr_pnl = sum((p.current_price - p.avg_price) * p.qty for p in kbal.positions)
    us_pnl = sum((p.current_price - p.avg_price) * p.qty for p in obal.positions) * fx
    total_krw = 500_000_000 + kr_pnl + us_pnl
    per = total_krw / top_n
    market = _open_market()

    orders = []  # (symbol, side, qty, market, position)
    for sym, (p, mk) in held.items():        # 타겟외 보유 매도(열린 시장만)
        if sym not in tsyms and p.qty > 0 and mk == market:
            orders.append((sym, Side.SELL, int(p.qty), mk, p))
    for t in targets:                        # 타겟 매수(열린 시장만)
        if t["market"] != market:
            continue
        f = fx if t["market"] == "US" else 1.0
        cur_krw = held[t["symbol"]][0].market_value * f if t["symbol"] in held else 0
        qty = int((per - cur_krw) // (t["price"] * f))
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
                lim = base * (1.01 if side == Side.BUY else 0.99)  # 마켓터블 지정가(체결유도)
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

    return {"total": total_krw, "market": market or "마감",
            "targets": [f"{t['symbol']}({t['market']})" for t in targets],
            "orders": executed}
