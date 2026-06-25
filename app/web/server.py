"""FastAPI 백엔드 — 봇 엔진을 JSON API로 노출(대시보드용).

엔진(brokers/screener/forecast/news/storage)을 재사용. 느린 호출(스크리너·예측)은
Redis 캐시(TTL)로 빠르게. 정적 프론트(React 빌드)는 /static, SPA는 / 로 서빙.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import redis
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from bot.brokers import get_broker
from bot.config import settings
from bot.storage.db import SessionLocal, init_db
from bot.storage.models import Prediction, Proposal, DailySnapshot, PaperSnapshot, OrderLog

log = logging.getLogger(__name__)
app = FastAPI(title="stock-bot API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])
_r = redis.from_url(settings.redis_url)


@app.on_event("startup")
def _startup():
    init_db()


def _cache_get(key: str):
    v = _r.get(key)
    return json.loads(v) if v else None


def _cache_set(key: str, val, ttl: int):
    _r.set(key, json.dumps(val, default=str), ex=ttl)


# ---------------- 기본 ----------------
@app.get("/api/health")
def health():
    return {"ok": True, "mode": settings.trading_mode, "broker": settings.broker,
            "ts": datetime.now().isoformat()}


@app.get("/api/portfolio")
def portfolio(broker: str = Query(default="")):
    b = get_broker(broker or None)
    fx = b.usdkrw()
    bal = b.get_balance()
    total_krw = bal.cash + sum(p.market_value_krw(fx) for p in bal.positions)
    # 토스 앱과 동일하게 토스 rate 사용. 금액은 rate에 맞춰 도출(토스 amount는 rate와 불일치).
    cost = sum(p.qty * p.avg_price for p in bal.positions)
    tp, dp = bal.total_pnl_pct, bal.daily_pnl_pct
    return {
        "broker": b.name, "fx": fx, "cash": bal.cash, "total_krw": total_krw,
        "daily_pnl_pct": dp, "total_pnl_pct": tp,
        "daily_pnl_amt_krw": round(total_krw * (dp / 100)) if dp is not None else None,
        "total_pnl_amt_krw": round(cost * (tp / 100) * fx) if (tp is not None and cost) else None,
        "positions": [{
            "symbol": p.symbol, "name": p.name, "qty": p.qty,
            "avg_price": p.avg_price, "price": p.current_price,
            "currency": p.currency, "pnl_pct": round(p.pnl_pct, 2),
            "value": p.market_value, "value_krw": p.market_value_krw(fx),
        } for p in bal.positions],
    }


@app.get("/api/news")
def news_summaries(symbols: str = Query(default=""), limit: int = 12):
    """종목별 뉴스 이슈 요약(Groq). 종목당 4시간 캐시."""
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:limit]
    from bot import news
    out = []
    for s in syms:
        c = _cache_get(f"web:news:{s}")
        if c is None:
            try:
                ns = news.get_sentiment(s, "US")
                c = {"symbol": s, "score": round(ns.score, 2),
                     "summary": ns.summary, "sources": ns.sources}
            except Exception:  # noqa: BLE001
                c = {"symbol": s, "score": 0, "summary": "(조회 오류)", "sources": 0}
            _cache_set(f"web:news:{s}", c, 4 * 3600)
        out.append(c)
    return out


@app.get("/api/kis")
def kis_accounts():
    """한투 실계좌 통합(ISA·연금=국내, 소수점=해외). 캐시 60초."""
    if c := _cache_get("web:kis"):
        return c
    from bot.brokers.kis import KISBroker
    from bot.brokers.toss import TossBroker
    fx = TossBroker().usdkrw() or 1540.0
    accts = [("63776023", "01", "ISA", "KR"), ("63776023", "22", "연금", "KR"),
             ("63751874", "01", "소수점", "US")]
    out = {"total": 0.0, "positions": [], "fx": fx}
    for cano, prod, label, mk in accts:
        try:
            b = KISBroker(account=(cano, prod), paper=False)  # 실전
            bal = b.get_overseas_balance() if mk == "US" else b.get_balance()
        except Exception as e:  # noqa: BLE001
            out.setdefault("errors", []).append(f"{label}: {e}")
            continue
        if mk == "KR":
            out["total"] += bal.total_eval
        for p in bal.positions:
            vkrw = p.market_value * (fx if p.currency == "USD" else 1)
            if mk == "US":
                out["total"] += vkrw
            out["positions"].append({
                "account": label, "market": mk, "symbol": p.symbol,
                "name": p.name, "qty": p.qty, "price": p.current_price,
                "currency": p.currency, "pnl_pct": round(p.pnl_pct, 2),
                "value_krw": vkrw})
    # 전체(누적) 수익률 = 평가합 / 매입합 - 1
    cost = sum(p["value_krw"] / (1 + p["pnl_pct"] / 100)
               for p in out["positions"] if p["pnl_pct"] > -100)
    val = sum(p["value_krw"] for p in out["positions"])
    out["total_pnl_pct"] = round((val / cost - 1) * 100, 2) if cost else None
    out["total_pnl_amt_krw"] = round(val - cost) if cost else None
    _cache_set("web:kis", out, 60)
    return out


@app.get("/api/quotes")
def quotes(symbols: str = Query(default="")):
    """보유종목 현재가 일괄(1회 호출). 5초 캐시. 대시보드 라이브 갱신용."""
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    if not syms:
        return {}
    key = "web:quotes:" + ",".join(sorted(syms))
    if c := _cache_get(key):
        return c
    out = get_broker().get_prices(syms)
    _cache_set(key, out, 5)
    return out


# ---------------- 스크리너 (캐시 1h) ----------------
@app.get("/api/screen")
def screen(refresh: bool = False):
    if not refresh and (c := _cache_get("web:screen")):
        return c
    from bot.screener import screen as run_screen, DEFAULT_WATCHLIST
    b = get_broker()
    candles = {}
    for s in DEFAULT_WATCHLIST:
        try:
            candles[s] = b.get_candles(s, "1d", 200)
        except Exception:  # noqa: BLE001
            pass
    results = run_screen(candles)
    out = [{
        "symbol": r.symbol, "score": round(r.score, 1), "last": r.last,
        "ret_1m": r.ret_1m, "ret_3m": r.ret_3m, "above_sma200": r.above_sma200,
        "trend_aligned": r.trend_aligned, "vol_20d": round(r.vol_20d, 2),
    } for r in results]
    _cache_set("web:screen", out, 3600)
    return out


# ---------------- 예측 (캐시 30m) ----------------
@app.get("/api/forecast/{symbol}")
def forecast(symbol: str, days: int = 21):
    symbol = symbol.upper()
    key = f"web:forecast:{symbol}:{days}"
    if c := _cache_get(key):
        return c
    from bot import news
    from bot.forecast import forecast_symbol
    from bot.screener import score_symbol
    b = get_broker()
    closes = [c["close"] for c in b.get_candles(symbol, "1d", 200) if c["close"] > 0]
    if len(closes) < 30:
        return {"error": "데이터 부족", "symbol": symbol, "candles": len(closes)}
    sc = score_symbol(symbol, [{"close": x} for x in closes])
    tech = max(-1.0, min(1.0, sc.score / 25.0)) if sc else 0.0
    fc = forecast_symbol(symbol, closes, days, technical_tilt=tech,
                         news_sentiment=news.tilt(symbol))
    out = {
        "symbol": symbol, "last": fc.last, "horizon_days": days,
        "prob_up": round(fc.prob_up, 3), "exp_return": round(fc.exp_return, 2),
        "band": fc.band, "target_touch": fc.target_touch, "down_touch": fc.down_touch,
        "exp_peak_pct": round(fc.exp_peak_pct, 2), "exp_trough_pct": round(fc.exp_trough_pct, 2),
        "backtest_winrate": fc.backtest_winrate, "backtest_n": fc.backtest_n,
        "daily_vol_pct": round(fc.daily_vol_pct, 2), "drift_parts": fc.drift_parts,
    }
    _cache_set(key, out, 1800)
    return out


# ---------------- DB 조회 ----------------
@app.get("/api/proposals")
def proposals(limit: int = 50):
    with SessionLocal() as s:
        rows = s.query(Proposal).order_by(Proposal.id.desc()).limit(limit).all()
        return [{
            "id": p.id, "ts": str(p.ts), "symbol": p.symbol, "side": p.side,
            "qty": p.qty, "currency": p.currency, "ref_price": p.ref_price,
            "reason": p.reason, "status": p.status,
        } for p in rows]


@app.get("/api/predictions")
def predictions(limit: int = 50):
    with SessionLocal() as s:
        rows = s.query(Prediction).order_by(Prediction.id.desc()).limit(limit).all()
        return [{
            "id": p.id, "made_at": str(p.made_at), "symbol": p.symbol,
            "horizon_days": p.horizon_days, "base_price": p.base_price,
            "prob_up": p.prob_up, "exp_return": p.exp_return,
            "status": p.status, "actual_return": p.actual_return,
            "dir_hit": p.dir_hit, "band_hit": p.band_hit,
        } for p in rows]


@app.get("/api/accuracy")
def accuracy():
    with SessionLocal() as s:
        ev = s.query(Prediction).filter(Prediction.status == "evaluated").all()
        if not ev:
            return {"evaluated": 0, "dir_acc": None, "band_acc": None}
        n = len(ev)
        return {
            "evaluated": n,
            "dir_acc": round(sum(1 for p in ev if p.dir_hit) / n, 3),
            "band_acc": round(sum(1 for p in ev if p.band_hit) / n, 3),
        }


@app.get("/api/paper")
def paper():
    """모의 자동매매 계좌(전략 검증) — 잔고·보유·성적표."""
    if c := _cache_get("web:paper"):
        return c
    from bot.brokers.kis import KISBroker
    from bot.brokers.toss import TossBroker
    out = {"cash": 0, "total": 0, "positions": [], "history": [], "ret_pct": None}
    fx = 1540.0
    try:
        kis = KISBroker(paper=True)
        fx = TossBroker().usdkrw() or 1540.0
        kbal = kis.get_balance()                 # KR
        obal = kis.get_overseas_balance()        # US
        kr_pnl = sum((p.current_price - p.avg_price) * p.qty for p in kbal.positions)
        us_pnl = sum((p.current_price - p.avg_price) * p.qty for p in obal.positions) * fx
        total = 500_000_000 + kr_pnl + us_pnl    # 초기 5억 + 손익(통합증거금 이중계산 방지)
        out["total"] = total
        out["positions"] = [{
            "symbol": p.symbol, "name": p.name, "qty": p.qty, "market": "KR",
            "price": p.current_price, "pnl_pct": round(p.pnl_pct, 2),
            "value_krw": p.market_value,
        } for p in kbal.positions] + [{
            "symbol": p.symbol, "name": p.name, "qty": p.qty, "market": "US",
            "price": p.current_price, "pnl_pct": round(p.pnl_pct, 2),
            "value_krw": p.market_value * fx,
        } for p in obal.positions]
        # 통합증거금 계좌라 dnca(kbal.cash)는 US 매수해도 5억 그대로 → 부정확.
        # 가용현금 = 총자산 - 보유평가합 으로 일관 계산(이중표시 방지).
        out["invested"] = sum(p["value_krw"] for p in out["positions"])
        out["cash"] = total - out["invested"]
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)
    with SessionLocal() as s:
        snaps = s.query(PaperSnapshot).order_by(PaperSnapshot.id.desc()).limit(90).all()
        out["history"] = [{"ts": str(x.ts), "total": x.total_eval}
                          for x in reversed(snaps)]
        # 거래내역 — 최근 40건(성공/실패 모두). 주문→실체결 라이프사이클 추적.
        rows = (s.query(OrderLog)
                .filter(OrderLog.mode == "paper")
                .order_by(OrderLog.id.desc()).limit(40).all())
        fills = {}  # US odno→체결현황. 실패해도 거래내역은 표시.
        try:
            from bot.brokers.kis import KISBroker
            kk = KISBroker(paper=True)
            fills = kk.overseas_fills(
                (datetime.now() - timedelta(days=4)).strftime("%Y%m%d"),
                datetime.now().strftime("%Y%m%d"))
        except Exception:  # noqa: BLE001
            pass
        trades = []
        for t in rows:
            mk = "US" if "us" in (t.broker or "") else "KR"
            item = {"ts": str(t.ts), "symbol": t.symbol, "side": t.side,
                    "qty": t.qty, "market": mk, "filled": None,
                    "fill_price": None, "amount_krw": None}
            if not t.ok:                                   # 접수 실패=거부
                msg = t.message or ""
                item["status"] = "거부"
                item["note"] = ("혼잡(재시도)" if "초당" in msg else
                                "장외" if "장시작" in msg or "장종료" in msg else
                                "서버오류" if "500" in msg or "Server" in msg else
                                "잔고부족" if "잔고" in msg else (msg[:14] or "실패"))
                item["filled"] = 0
            else:
                oid = int(t.order_id) if (t.order_id or "").isdigit() else None
                f = fills.get(oid) if oid else None
                if f is not None:                          # US 체결현황 매칭됨
                    item["filled"] = f["ccld"]
                    item["status"] = ("체결" if f["ccld"] >= f["ord"] > 0 else
                                      "부분체결" if f["ccld"] > 0 else "미체결")
                    if f["ccld"] > 0:                       # 실체결 단가·거래대금
                        item["fill_price"] = round(f["ccld_prc"], 2)
                        item["amount_krw"] = round(f["ccld_amt"] * fx)
                elif mk == "KR":                           # KR 시장가=즉시체결
                    item["filled"] = t.qty
                    item["status"] = "체결"
                    if t.price:                             # KR은 OrderLog.price(있으면)
                        item["fill_price"] = t.price
                        item["amount_krw"] = round(t.price * t.qty)
                else:                                      # US인데 조회범위 밖
                    item["status"] = "접수"
            trades.append(item)
        out["trades"] = trades
        # 총 거래대금(체결된 매수+매도 절대금액 합, ₩)
        out["turnover_krw"] = round(sum(x["amount_krw"] or 0 for x in trades))
    if out["total"]:
        out["ret_pct"] = round((out["total"] / 500_000_000 - 1) * 100, 2)  # 초기 5억
    _cache_set("web:paper", out, 30)
    return out


@app.get("/api/snapshots")
def snapshots(limit: int = 60):
    with SessionLocal() as s:
        rows = s.query(DailySnapshot).order_by(DailySnapshot.id.desc()).limit(limit).all()
        return [{"ts": str(x.ts), "cash": x.cash, "total_eval": x.total_eval}
                for x in reversed(rows)]


# ---------------- 정적 프론트(React 빌드) ----------------
_STATIC = Path(__file__).resolve().parent / "static"
if (_STATIC / "assets").exists():
    app.mount("/assets", StaticFiles(directory=_STATIC / "assets"), name="assets")
if (_STATIC / "index.html").exists():
    @app.get("/")
    def index():
        return FileResponse(_STATIC / "index.html")
