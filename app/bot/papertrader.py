"""모의계좌 자동매매 — '예측+스크리너 종합' 전략을 KIS 모의계좌(가상 5억)에 실제 실행.

목적: 우리가 설계한 전략이 진짜 수익을 내는지 실돈 위험 0으로 검증.
전략: KR ETF 유니버스를 스크리너 점수 + 예측 상승확률로 종합 랭킹 →
      상위 N종목 동일가중 보유 → 주기적 리밸런싱(타겟이탈 매도+목표 매수).
실행: KISBroker(paper=True)로 모의계좌에 시장가 주문(장중 체결).
추적: 매 실행 PaperSnapshot 기록 → 대시보드에서 전략 성적표.
"""
from __future__ import annotations

import logging
import time

from bot.brokers.base import Side
from bot.brokers.kis import KISBroker
from bot.forecast import forecast_symbol
from bot.screener import score_symbol
from bot.storage.db import SessionLocal
from bot.storage.models import OrderLog, PaperSnapshot

log = logging.getLogger(__name__)

# 모의 KR ETF 유니버스 (국내상장·유동성, 모의 거래가능)
PAPER_UNIVERSE = ["458730", "360750", "133690", "069500", "379800",
                  "161510", "329200", "273130"]
TOP_N = 4


def _rank(broker) -> list[dict]:
    """스크리너 점수 + 예측 상승확률 종합 랭킹."""
    ranked = []
    for sym in PAPER_UNIVERSE:
        try:
            closes = [c["close"] for c in broker.get_candles(sym, "1d", 200)
                      if c["close"] > 0]
        except Exception:  # noqa: BLE001
            continue
        if len(closes) < 40:
            continue
        sc = score_symbol(sym, [{"close": x} for x in closes])
        if not sc:
            continue
        fc = forecast_symbol(sym, closes, 21,
                             technical_tilt=max(-1, min(1, sc.score / 25)),
                             news_sentiment=0)
        prob = fc.prob_up if fc else 0.5
        combined = sc.score + (prob - 0.5) * 100  # 스크리너 + 예측 블렌드
        ranked.append({"symbol": sym, "score": combined, "price": closes[-1],
                       "prob_up": round(prob, 3), "scr": round(sc.score, 1)})
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked


def run_paper(top_n: int = TOP_N, dry: bool = False) -> dict:
    broker = KISBroker(paper=True)  # 항상 모의(실전모드여도 안전)
    bal = broker.get_balance()
    total = bal.cash + sum(p.market_value for p in bal.positions)
    if total <= 0:
        return {"error": "잔고 0"}

    ranked = _rank(broker)
    if not ranked:
        return {"error": "랭킹 산출 실패"}
    targets = ranked[:top_n]
    tsyms = {t["symbol"] for t in targets}
    held = {p.symbol: p for p in bal.positions}
    per = total / top_n

    orders = []  # (symbol, side, qty, reason)
    for p in bal.positions:                       # 타겟 외 보유 전량매도
        if p.symbol not in tsyms and p.qty > 0:
            orders.append((p.symbol, Side.SELL, int(p.qty), "타겟이탈"))
    for t in targets:                             # 목표비중까지 매수
        cur = held[t["symbol"]].market_value if t["symbol"] in held else 0
        qty = int((per - cur) // t["price"])
        if qty > 0:
            orders.append((t["symbol"], Side.BUY, qty, f"목표 {per:,.0f}원"))

    executed = []
    with SessionLocal() as session:
        for sym, side, qty, reason in orders:
            if dry:
                executed.append(f"[DRY] {side.value} {sym} {qty}")
                continue
            res = broker.place_order(sym, side, qty)  # 시장가
            time.sleep(0.4)  # KIS 초당거래 제한 회피
            executed.append(f"{'✅' if res.ok else '❌'} {side.value} {sym} {qty}: {res.message}")
            session.add(OrderLog(broker="kis-paper", mode="paper", symbol=sym,
                                 side=side.value, qty=qty, ok=res.ok,
                                 order_id=res.order_id, reason=reason,
                                 message=(res.message or "")[:250]))
        if not dry:
            session.add(PaperSnapshot(cash=bal.cash, total_eval=total,
                                      holdings=len(bal.positions)))
        session.commit()

    return {"total": total, "targets": [t["symbol"] for t in targets],
            "ranked": ranked, "orders": executed}
