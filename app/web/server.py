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
from bot.storage.models import Prediction, Proposal, DailySnapshot

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
    return {
        "broker": b.name, "fx": fx, "cash": bal.cash, "total_krw": total_krw,
        "positions": [{
            "symbol": p.symbol, "name": p.name, "qty": p.qty,
            "avg_price": p.avg_price, "price": p.current_price,
            "currency": p.currency, "pnl_pct": round(p.pnl_pct, 2),
            "value": p.market_value, "value_krw": p.market_value_krw(fx),
        } for p in bal.positions],
    }


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
