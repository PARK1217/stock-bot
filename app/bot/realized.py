"""실현손익(매도 기준) 일별·월별 집계 — 이동평균단가 방식.

증권사가 매도별 실현손익을 직접 주지 않으므로, 매수/매도를 시각순으로 처리하며
종목별 평균단가를 추적 → 매도 시 (매도가 - 평단가) × 수량 - 수수료 = 실현손익.
계좌별 + 전체 합산, 일별/월별. ⚠️ 조회 범위(토스 최근 100건·KIS 1년) 안의 매수까지만
평단가가 정확(그 전 매수분은 근사). 모의 국내는 체결조회 미지원 → OrderLog 기준가(근사).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

log = logging.getLogger(__name__)


# ---------------- 이동평균 실현손익 엔진 ----------------
def _engine(trades: list[dict], fx: float) -> list[dict]:
    """trades: [{ts, symbol, side('BUY'/'SELL'), qty, price, fee, currency}] (정렬 무관).
    반환: 매도 건별 실현손익 리스트."""
    pos: dict[str, dict] = {}
    sells = []
    for t in sorted(trades, key=lambda x: x.get("ts") or ""):
        sym = t.get("symbol") or ""
        side = (t.get("side") or "").upper()
        qty = float(t.get("qty") or 0)
        price = float(t.get("price") or 0)
        fee = float(t.get("fee") or 0)
        cur = t.get("currency", "USD")
        ts = t.get("ts") or ""
        if not sym or qty <= 0 or len(ts) < 7:
            continue
        p = pos.setdefault(sym, {"qty": 0.0, "cost": 0.0})
        if side == "BUY":
            p["qty"] += qty
            p["cost"] += qty * price + fee
        else:  # SELL
            avg = (p["cost"] / p["qty"]) if p["qty"] > 1e-9 else price
            buy_cost = avg * qty
            realized = (qty * price - fee) - buy_cost          # 매도대금 - 원가(평단)
            rk = realized * (fx if cur == "USD" else 1)
            sells.append({
                "date": ts[:10], "month": ts[:7], "symbol": sym, "qty": round(qty, 4),
                "buy_avg": round(avg, 4), "sell_price": round(price, 4), "currency": cur,
                "realized": round(realized, 2), "realized_krw": round(rk),
            })
            p["qty"] -= qty
            p["cost"] -= buy_cost
            if p["qty"] <= 1e-9:
                p["qty"], p["cost"] = 0.0, 0.0
    return sells


def _group(sells: list[dict]) -> tuple[dict, dict]:
    daily, monthly = {}, {}
    for s in sells:
        daily[s["date"]] = daily.get(s["date"], 0) + s["realized_krw"]
        monthly[s["month"]] = monthly.get(s["month"], 0) + s["realized_krw"]
    return daily, monthly


# ---------------- 계좌별 거래 정규화 ----------------
def _toss_trades(broker) -> list[dict]:
    out = []
    for o in broker.order_history(100):
        if o.get("pending") or not o.get("qty"):
            continue
        out.append({"ts": o.get("at", ""), "symbol": o.get("symbol", ""),
                    "side": (o.get("side") or "").upper(), "qty": o.get("qty", 0),
                    "price": o.get("price", 0), "fee": o.get("fee", 0),
                    "currency": o.get("currency", "USD")})
    return out


def _kis_overseas(broker, start, end) -> list[dict]:
    out = []
    for r in broker.overseas_orders(start, end):
        q = float(r.get("ccld_qty") or 0)
        if q <= 0:
            continue
        dt = r.get("dt", "")                                   # YYYYMMDDHHMMSS
        ts = f"{dt[:4]}-{dt[4:6]}-{dt[6:8]} {dt[8:10]}:{dt[10:12]}" if len(dt) >= 12 else dt
        out.append({"ts": ts, "symbol": r.get("symbol", ""),
                    "side": "BUY" if r.get("side") == "buy" else "SELL",
                    "qty": q, "price": r.get("price", 0), "fee": 0, "currency": "USD"})
    return out


def _kis_domestic(broker, start, end) -> list[dict]:
    out = []
    for o in broker.domestic_orders(start, end):
        q = float(o.get("filled") or 0)
        if q <= 0:
            continue
        out.append({"ts": o.get("at", ""), "symbol": o.get("symbol", ""),
                    "side": o.get("side", "BUY"), "qty": q, "price": o.get("price", 0),
                    "fee": 0, "currency": "KRW"})
    return out


def _paper_kr() -> list[dict]:
    """모의 국내(inquire-daily-ccld 미지원) → OrderLog 체결성공분. price=기준가(근사)."""
    from bot.storage.db import SessionLocal
    from bot.storage.models import OrderLog
    out = []
    try:
        with SessionLocal() as s:
            for r in s.query(OrderLog).filter(
                    OrderLog.mode == "paper",
                    OrderLog.broker.like("kis-paper-kr%"),
                    OrderLog.ok.is_(True)).all():
                out.append({"ts": str(r.ts)[:16], "symbol": r.symbol,
                            "side": (r.side or "").upper(), "qty": float(r.qty or 0),
                            "price": float(r.price or 0), "fee": 0, "currency": "KRW"})
    except Exception as e:  # noqa: BLE001
        log.warning("paper_kr OrderLog: %s", e)
    return out


# ---------------- 리포트 조립 ----------------
def report(fx: float, today: datetime) -> dict:
    """모든 계좌 실현손익 → {accounts:{name:{sells,daily,monthly,total_krw}}, total, fx}."""
    from bot.brokers.kis import KISBroker
    from bot.brokers.toss import TossBroker, toss_spouse
    s365 = (today - timedelta(days=365)).strftime("%Y%m%d")
    e = today.strftime("%Y%m%d")
    accounts: dict[str, dict] = {}

    def add(name, trades):
        sells = _engine(trades, fx)
        d, m = _group(sells)
        accounts[name] = {"sells": sells, "daily": d, "monthly": m,
                          "total_krw": round(sum(s["realized_krw"] for s in sells))}

    for name, fn in (
        ("모의", lambda: _kis_overseas(KISBroker(paper=True), s365, e) + _paper_kr()),
        ("토스(나)", lambda: _toss_trades(TossBroker())),
    ):
        try:
            add(name, fn())
        except Exception as ex:  # noqa: BLE001
            log.warning("realized %s: %s", name, ex)

    sp = toss_spouse()
    if sp:
        try:
            add("토스(남편)", _toss_trades(sp))
        except Exception as ex:  # noqa: BLE001
            log.warning("realized 남편: %s", ex)

    try:
        from bot.accounts import account_registry
        kr = []
        for a in account_registry():
            br = KISBroker(account=a.acct, paper=False)
            kr += (_kis_overseas(br, s365, e) if a.overseas
                   else _kis_domestic(br, s365, e))
        add("한투", kr)
    except Exception as ex:  # noqa: BLE001
        log.warning("realized 한투: %s", ex)

    tot_d, tot_m = {}, {}
    for a in accounts.values():
        for k, v in a["daily"].items():
            tot_d[k] = tot_d.get(k, 0) + v
        for k, v in a["monthly"].items():
            tot_m[k] = tot_m.get(k, 0) + v
    return {"accounts": accounts,
            "total": {"daily": tot_d, "monthly": tot_m,
                      "total_krw": round(sum(a["total_krw"] for a in accounts.values()))},
            "fx": fx}
