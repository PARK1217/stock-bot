"""FastAPI 백엔드 — 봇 엔진을 JSON API로 노출(대시보드용).

엔진(brokers/screener/forecast/news/storage)을 재사용. 느린 호출(스크리너·예측)은
Redis 캐시(TTL)로 빠르게. 정적 프론트(React 빌드)는 /static, SPA는 / 로 서빙.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import hashlib
import hmac

import redis
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
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

# ---------------- 외부접속 인증(비번, 쿠키) ----------------
_PW = settings.dashboard_password
_AUTH_COOKIE = "sb_auth"
_AUTH_TOKEN = hashlib.sha256(f"stockbot::{_PW}".encode()).hexdigest()[:40] if _PW else ""
_LOGIN_HTML = """<!doctype html><html lang=ko><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>stock-bot</title>
<style>body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
background:#0f1115;color:#e6e9ef;font-family:system-ui,sans-serif}
.box{background:#1a1d24;border:1px solid #2a2f3a;border-radius:14px;padding:28px 24px;width:280px;text-align:center}
h1{font-size:20px;margin:0 0 18px}input{width:100%;box-sizing:border-box;background:#11141a;border:1px solid #2a2f3a;
color:#e6e9ef;border-radius:8px;padding:11px;font-size:15px;margin-bottom:10px}
button{width:100%;background:#4c8dff;color:#fff;border:0;border-radius:8px;padding:11px;font-size:15px;font-weight:600;cursor:pointer}
.err{color:#ff5c6c;font-size:13px;height:16px;margin-top:8px}</style>
<div class=box><h1>📈 stock-bot</h1>
<input id=pw type=password placeholder="비밀번호" autofocus
onkeydown="if(event.key==='Enter')go()">
<button onclick=go()>접속</button><div class=err id=e></div></div>
<script>async function go(){const p=document.getElementById('pw').value;
const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({password:p})});
if(r.ok)location.reload();else document.getElementById('e').textContent='비밀번호가 틀렸어요';}</script>
</html>"""
_AUTH_FREE = ("/api/login", "/api/health", "/sw.js", "/assets/manifest.webmanifest")


@app.middleware("http")
async def _auth(request: Request, call_next):
    if not _PW:                                    # 비번 미설정 = 인증 비활성(집망내)
        return await call_next(request)
    path = request.url.path
    if path in _AUTH_FREE or path.startswith("/assets/"):
        return await call_next(request)
    if hmac.compare_digest(request.cookies.get(_AUTH_COOKIE) or "", _AUTH_TOKEN):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return HTMLResponse(_LOGIN_HTML, status_code=401)


@app.post("/api/login")
def login(body: dict):
    if _PW and hmac.compare_digest(str(body.get("password") or ""), _PW):
        resp = JSONResponse({"ok": True})
        resp.set_cookie(_AUTH_COOKIE, _AUTH_TOKEN, max_age=60 * 60 * 24 * 30,
                        httponly=True, samesite="lax")
        return resp
    return JSONResponse({"ok": False}, status_code=401)


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
def portfolio(broker: str = Query(default=""), who: str = Query(default="me")):
    who = "spouse" if who == "spouse" else "me"
    ck = f"web:portfolio:{who}"
    if (c := _cache_get(ck)):
        return c
    if who == "spouse":
        from bot.brokers.toss import toss_spouse
        b = toss_spouse()
        if b is None:
            return {"error": "남편 계좌 미설정", "positions": [], "cash": 0,
                    "total_krw": 0, "fx": 1540, "broker": "toss"}
    else:
        b = get_broker(broker or None)
    fx = b.usdkrw()
    bal = b.get_balance()
    total_krw = bal.cash + sum(p.market_value_krw(fx) for p in bal.positions)
    # 토스 앱과 동일하게 토스 rate 사용. 금액은 rate에 맞춰 도출(토스 amount는 rate와 불일치).
    cost = sum(p.qty * p.avg_price for p in bal.positions)
    tp, dp = bal.total_pnl_pct, bal.daily_pnl_pct
    out = {
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
    _cache_set(ck, out, 60)
    return out


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
    out = {"total": 0.0, "cash_krw": 0.0, "positions": [], "fx": fx}
    for cano, prod, label, mk in accts:
        try:
            b = KISBroker(account=(cano, prod), paper=False)  # 실전
            bal = b.get_overseas_balance() if mk == "US" else b.get_balance()
        except Exception as e:  # noqa: BLE001
            out.setdefault("errors", []).append(f"{label}: {e}")
            continue
        if mk == "KR":
            out["total"] += bal.total_eval
            out["cash_krw"] += bal.cash            # 예수금(현금) 합산
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
    # 오늘 손익 = KR 종목 전일종가 대비(전일종가 1h 캐시). 한투 daily는 API 미제공 → 직접 산출
    daily = 0.0
    _kb = None
    for p in out["positions"]:
        if p["currency"] != "KRW":
            continue
        pc = _r.get(f"web:prevclose:{p['symbol']}")
        prev = float(pc) if pc is not None else 0.0
        if not prev:
            try:
                from bot.brokers.kis import KISBroker
                if _kb is None:
                    _kb = KISBroker(account=("63776023", "01"), paper=False)
                cs = _kb.get_candles(p["symbol"], "1d", 5)
                prev = cs[-2]["close"] if len(cs) >= 2 else 0.0
                if prev:
                    _r.set(f"web:prevclose:{p['symbol']}", prev, ex=3600)
            except Exception:  # noqa: BLE001
                prev = 0.0
        if prev:
            daily += p["qty"] * (p["price"] - prev)
    out["daily_pnl_amt_krw"] = round(daily)
    base_today = out["total"] - daily
    out["daily_pnl_pct"] = round(daily / base_today * 100, 2) if base_today else None
    _cache_set("web:kis", out, 60)
    return out


@app.get("/api/market-status")
def market_status():
    """오늘 개장 여부 — KR=KIS 휴장일 API(음력공휴일 정확), US는 프론트 하드코딩. 6h 캐시."""
    if (c := _cache_get("web:mktstat")):
        return c
    out = {"kr_open": True}
    try:
        from bot.brokers.kis import KISBroker
        k = KISBroker(account=("63776023", "01"), paper=False)
        today = datetime.now().strftime("%Y%m%d")
        resp = k._get("/uapi/domestic-stock/v1/quotations/chk-holiday",
                      k._headers("CTCA0903R"),
                      {"BASS_DT": today, "CTX_AREA_NK": "", "CTX_AREA_FK": ""})
        for r in resp.json().get("output", []) or []:
            if r.get("bass_dt") == today:
                out["kr_open"] = (r.get("opnd_yn") == "Y")
                break
    except Exception:  # noqa: BLE001
        pass
    _cache_set("web:mktstat", out, 21600)
    return out


@app.get("/api/exposure")
def exposure():
    """실질 노출(룩스루) — 실계좌(토스+한투) 보유 ETF를 구성종목으로 뚫어 실제 기업
    노출 합산. '분산 착시'(여러 ETF지만 속은 같은 대형주) 점검. 5분 캐시."""
    if (c := _cache_get("web:expo")):
        return c
    from bot.etf import lookthrough, top_constituents
    hold = []                                        # (symbol, value_krw)
    partial = False
    try:
        for x in portfolio("").get("positions", []):
            hold.append((x["symbol"], x["value_krw"]))
    except Exception:  # noqa: BLE001
        partial = True
    try:
        for x in kis_accounts().get("positions", []):
            hold.append((x["symbol"], x["value_krw"]))
    except Exception:  # noqa: BLE001
        partial = True
    total = sum(v for _, v in hold) or 1
    expo: dict[str, float] = {}
    for sym, v in hold:
        cons = top_constituents(sym, 10) if lookthrough(sym) else None
        if cons:                                     # ETF → 구성종목으로 분해
            wsum = sum(w for _, w in cons) or 1
            for c, w in cons:
                expo[c] = expo.get(c, 0) + v * (w / wsum)
        else:                                        # 개별주/룩스루 없음 → 그대로
            expo[sym] = expo.get(sym, 0) + v
    ranked = sorted(expo.items(), key=lambda x: -x[1])
    out = {"total": round(total), "n": len(expo), "partial": partial,
           "top": [{"symbol": s, "krw": round(v), "pct": round(v / total * 100, 1)}
                   for s, v in ranked[:12]],
           "top5_pct": round(sum(v for _, v in ranked[:5]) / total * 100, 1)}
    _cache_set("web:expo", out, 300)
    return out


@app.get("/api/quotes")
def quotes(symbols: str = Query(default=""), who: str = Query(default="me")):
    """보유종목 현재가 일괄(1회 호출). 5초 캐시. 대시보드 라이브 갱신용."""
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    if not syms:
        return {}
    who = "spouse" if who == "spouse" else "me"
    key = f"web:quotes:{who}:" + ",".join(sorted(syms))
    if c := _cache_get(key):
        return c
    if who == "spouse":
        from bot.brokers.toss import toss_spouse
        b = toss_spouse()
        out = b.get_prices(syms) if b else {}
    else:
        out = get_broker().get_prices(syms)
    _cache_set(key, out, 5)
    return out


# ---------------- 스크리너 (캐시 1h) ----------------
@app.get("/api/screen")
def screen(refresh: bool = False, market: str = "us"):
    market = "kr" if market.lower() == "kr" else "us"
    key = f"web:screen:{market}"
    if not refresh and (c := _cache_get(key)):
        return c
    from bot.screener import screen as run_screen, DEFAULT_WATCHLIST, KR_WATCHLIST
    if market == "kr":
        from bot.brokers.kis import KISBroker
        b = KISBroker(account=("63776023", "01"), paper=False)   # KR 시세용
        wl = KR_WATCHLIST
    else:
        b = get_broker()
        wl = DEFAULT_WATCHLIST
    candles = {}
    for s in wl:
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
    _cache_set(key, out, 3600)
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
    if symbol[:1].isdigit():                       # 국내(숫자코드)는 KIS 캔들로
        from bot.brokers.kis import KISBroker
        b = KISBroker(paper=False)
    else:
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
    # KR·US 잔고를 독립적으로 조회 — KIS 모의 국내(inquire-balance)가 간헐 500을 내도
    # US 보유까지 통째로 0이 되지 않게 분리(한쪽 실패해도 나머지는 표시).
    kr_pos, us_pos, errs = [], [], []
    kr_pnl = us_pnl = 0.0
    kis = None
    try:
        kis = KISBroker(paper=True)
        fx = TossBroker().usdkrw() or 1540.0
    except Exception as e:  # noqa: BLE001
        errs.append(f"init:{str(e)[:120]}")
    if kis is not None:
        try:
            kr_pos = kis.get_balance().positions                     # KR
            kr_pnl = sum((p.current_price - p.avg_price) * p.qty for p in kr_pos)
        except Exception as e:  # noqa: BLE001  (KIS 모의 국내 간헐 500)
            errs.append(f"KR:{str(e)[:120]}")
        try:
            us_pos = kis.get_overseas_balance().positions            # US
            us_pnl = sum((p.current_price - p.avg_price) * p.qty for p in us_pos) * fx
        except Exception as e:  # noqa: BLE001
            errs.append(f"US:{str(e)[:120]}")
    if kr_pos or us_pos or not errs:
        total = 500_000_000 + kr_pnl + us_pnl    # 초기 5억 + 손익(통합증거금 이중계산 방지)
        out["total"] = total
        out["positions"] = [{
            "symbol": p.symbol, "name": p.name, "qty": p.qty, "market": "KR",
            "price": p.current_price, "pnl_pct": round(p.pnl_pct, 2),
            "value_krw": p.market_value,
        } for p in kr_pos] + [{
            "symbol": p.symbol, "name": p.name, "qty": p.qty, "market": "US",
            "price": p.current_price, "pnl_pct": round(p.pnl_pct, 2),
            "value_krw": p.market_value * fx,
        } for p in us_pos]
        # 통합증거금 계좌라 dnca(kbal.cash)는 US 매수해도 5억 그대로 → 부정확.
        # 가용현금 = 총자산 - 보유평가합 으로 일관 계산(이중표시 방지).
        out["invested"] = sum(p["value_krw"] for p in out["positions"])
        out["cash"] = total - out["invested"]
        st = _r.get("paper:strategy")                    # 전략 현황(코어/새틀 선정)
        if st:
            strat = json.loads(st)
            out["strategy"] = strat
            core, sat = set(strat.get("core", [])), set(strat.get("sat", []))
            for p in out["positions"]:                    # 보유에 코어/새틀/이탈 태그
                p["bucket"] = ("core" if p["symbol"] in core else
                               "sat" if p["symbol"] in sat else "exit")
    if errs:                                              # 부분/전체 실패 표시(US만 떠도 KR오류 노출)
        out["error"] = " · ".join(errs)
    with SessionLocal() as s:
        snaps = s.query(PaperSnapshot).order_by(PaperSnapshot.id.desc()).limit(90).all()
        out["history"] = [{"ts": str(x.ts), "total": x.total_eval}
                          for x in reversed(snaps)]
    if out["total"]:
        out["ret_pct"] = round((out["total"] / 500_000_000 - 1) * 100, 2)  # 초기 5억
    _cache_set("web:paper", out, 30 if out["total"] else 5)   # 실패 시 짧게 캐싱→빠른 회복
    return out


def _fmt_dt(s: str) -> str:
    """ccnl ord_dt+ord_tmd(YYYYMMDDHHMMSS) → 'MM-DD HH:MM'."""
    return f"{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}" if len(s) >= 12 else s


@app.get("/api/screen/backtest")
def screen_backtest():
    """스크리너 점수 신뢰도 — 점수 vs 이후수익 백테스트(IC·스프레드·적중). 24h 캐시."""
    if (c := _cache_get("web:scn:bt")):
        return c
    from bot.screener import backtest_score, DEFAULT_WATCHLIST
    b = get_broker()
    candles = {}
    for s in DEFAULT_WATCHLIST:
        try:
            candles[s] = b.get_candles(s, "1d", 200)
        except Exception:  # noqa: BLE001
            pass
    out = backtest_score(candles)
    _cache_set("web:scn:bt", out, 86400)
    return out


@app.get("/api/backtest")
def backtest_view():
    """전략 백테스트 결과(주1회 cmd_backtest가 redis 기록): 점수 모멘텀 vs 평균회귀 vs
    벤치 +유의성 t값, 돌파 fee 민감도. 미실행이면 status 안내."""
    if (c := _cache_get("backtest:strategy")):
        return c
    return {"status": "미실행", "hint": "python -m bot.main backtest"}


@app.get("/api/glossary")
def glossary_view():
    """주린이용 용어 사전(term→쉬운 풀이). 대시보드 '쉬운 용어' 도움말이 사용."""
    from bot.glossary import TERMS
    return {"terms": TERMS}


@app.get("/api/cash")
def cash(who: str = "me"):
    """실계좌 현금 잔고 — 원화·달러(토스 매수가능금액). 60초 캐시."""
    ck = f"web:cash:{who}"
    if (c := _cache_get(ck)):
        return c
    from bot.brokers.toss import TossBroker, toss_spouse
    b = toss_spouse() if who == "spouse" else TossBroker()
    out = b.cash_balances() if b else {"KRW": 0, "USD": 0}
    _cache_set(ck, out, 60)
    return out


@app.get("/api/orders")
def orders_view(who: str = "me", limit: int = 20):
    """실계좌 주문/거래 내역(체결 완료분). 120초 캐시."""
    ck = f"web:orders:{who}"
    if (c := _cache_get(ck)):
        return c
    from bot.brokers.toss import TossBroker, toss_spouse
    b = toss_spouse() if who == "spouse" else TossBroker()
    out = {"orders": b.order_history(limit) if b else []}
    _cache_set(ck, out, 60)
    return out


@app.get("/api/names")
def names_view():
    """종목명 맵 — 보유(토스 나/남편·모의)에서 증권사가 준 이름을 학습(Redis 영구)해 반환.
    한 번 보유한 종목은 팔아도 이름이 남아 거래내역·노출 등에서 자동 표시(코드 수정 불필요).
    프론트는 KNM(한글 시드) → 현재보유 → 이 학습맵(영어) 순으로 사용. 60초 캐시."""
    from bot import names as N
    if (c := _cache_get("web:names")):
        return c
    for getter in (lambda: portfolio("", "me"), lambda: portfolio("", "spouse"), paper):
        try:
            N.learn_positions((getter() or {}).get("positions", []))
        except Exception:  # noqa: BLE001  (학습 실패는 무시)
            pass
    out = {"names": N.all_learned()}
    _cache_set("web:names", out, 60)
    return out


@app.get("/api/realized")
def realized_view():
    """실현손익(매도 기준) 계좌별+전체 일별·월별. 평단가 이동평균 계산. 300초 캐시."""
    if (c := _cache_get("web:realized")):
        return c
    from bot import realized
    from bot.brokers.toss import TossBroker
    fx = TossBroker().usdkrw() or 1540.0
    out = realized.report(fx, datetime.now())
    _cache_set("web:realized", out, 300)
    return out


@app.get("/api/kis/orders")
def kis_orders(limit: int = 40):
    """한투 국내(ISA·연금) 주문·거래 내역. 최근 40일, 대기(미체결) 먼저. 180초 캐시."""
    if (c := _cache_get("web:kisord")):
        return c
    from bot.brokers.kis import KISBroker
    end = datetime.now()
    s, e = (end - timedelta(days=40)).strftime("%Y%m%d"), end.strftime("%Y%m%d")
    rows = []
    for cano, prod, label in [("63776023", "01", "ISA"), ("63776023", "22", "연금")]:
        try:
            b = KISBroker(account=(cano, prod), paper=False)
            for o in b.domestic_orders(s, e):
                o["account"] = label
                rows.append(o)
        except Exception as ex:  # noqa: BLE001
            log.warning("kis orders %s 실패: %s", label, ex)
    rows.sort(key=lambda x: x.get("at", ""), reverse=True)    # 최신순
    rows.sort(key=lambda x: 0 if x.get("pending") else 1)     # 대기 먼저(안정정렬)
    out = {"orders": rows[:limit]}
    _cache_set("web:kisord", out, 180)
    return out


@app.get("/api/paper/trades")
def paper_trades(page: int = 0, size: int = 8):
    """모의 거래내역 — US=KIS 체결원장(ccnl), KR=OrderLog(모의 국내 체결조회 미지원).
    매수·매도 통합, 시각순 페이징."""
    page = max(0, page); size = min(max(size, 1), 50)
    if (c := _cache_get(f"web:ptr:{page}:{size}")):
        return c
    fx = 1540.0
    rows = []                                            # (정렬키 dt, item)
    turnover = 0.0
    try:
        from bot.brokers.kis import KISBroker
        from bot.brokers.toss import TossBroker
        fx = TossBroker().usdkrw() or 1540.0
        for r in KISBroker(paper=True).overseas_orders(            # US (ccnl)
                (datetime.now() - timedelta(days=10)).strftime("%Y%m%d"),
                datetime.now().strftime("%Y%m%d")):
            ccld, ordq = r["ccld_qty"], r["ord_qty"]
            amt = round(r["amt"] * fx) if ccld > 0 else None
            if ccld > 0:
                turnover += (r["amt"] or 0) * fx          # 체결분만(KR과 기준 통일)
            rows.append((r["dt"], {
                "ts": _fmt_dt(r["dt"]), "symbol": r["symbol"], "side": r["side"],
                "qty": ordq, "filled": ccld, "market": "US",
                "status": ("체결" if ccld >= ordq > 0 else "부분체결" if ccld > 0 else "미체결"),
                "fill_price": round(r["price"], 2) if ccld > 0 else None, "amount_krw": amt}))
    except Exception:  # noqa: BLE001
        pass
    try:                                                          # KR (OrderLog)
        with SessionLocal() as s:
            kr = (s.query(OrderLog)
                  .filter(OrderLog.mode == "paper", OrderLog.ok.is_(True),
                          OrderLog.broker.notlike("%us%"))
                  .order_by(OrderLog.id.desc()).limit(300).all())
            for t in kr:
                dt = (t.ts + timedelta(hours=9)).strftime("%Y%m%d%H%M%S")  # UTC→KST
                amt = round((t.price or 0) * t.qty) if t.price else None
                if amt:
                    turnover += amt
                rows.append((dt, {
                    "ts": _fmt_dt(dt), "symbol": t.symbol, "side": t.side,
                    "qty": t.qty, "filled": t.qty, "market": "KR", "status": "체결",
                    "fill_price": (t.price or None), "amount_krw": amt}))
    except Exception:  # noqa: BLE001
        pass
    rows.sort(key=lambda x: x[0], reverse=True)          # 최신순
    items = [it for _, it in rows]
    total = len(items)
    out = {"items": items[page * size:(page + 1) * size], "page": page, "size": size,
           "total": total, "pages": (total + size - 1) // size if total else 0,
           "turnover_krw": round(turnover)}
    _cache_set(f"web:ptr:{page}:{size}", out, 20)
    return out


@app.get("/api/snapshots")
def snapshots(limit: int = 60):
    with SessionLocal() as s:
        rows = s.query(DailySnapshot).order_by(DailySnapshot.id.desc()).limit(limit).all()
        return [{"ts": str(x.ts), "cash": x.cash, "total_eval": x.total_eval}
                for x in reversed(rows)]


@app.get("/api/assets")
def assets(limit: int = 90):
    """실계좌 자산 흐름 — 전체 + 계좌별(토스·ISA·연금) 시계열. 매일 장마감 기록."""
    from bot.storage.models import AssetSnapshot
    with SessionLocal() as s:
        rows = list(reversed(
            s.query(AssetSnapshot).order_by(AssetSnapshot.id.desc()).limit(limit).all()))
    return {
        "ts": [str(x.ts)[:10] for x in rows],
        "series": {
            "전체 자산": [round(x.total_krw) for x in rows],
            "토스(미국)": [round(x.toss_krw) for x in rows],
            "ISA": [round(x.isa_krw) for x in rows],
            "연금": [round(x.pension_krw) for x in rows],
        },
    }


def _chat_context(who: str = "me") -> str:
    """챗봇 근거 데이터 — 보유·스크리너·신뢰도·계좌제약. who=spouse면 남편 토스만."""
    L = []
    sp = who == "spouse"
    try:
        p = portfolio("", "spouse") if sp else portfolio("")
        lbl = "남편 토스 계좌(미국)" if sp else "토스 실계좌(미국)"
        toss_tot = (p.get("cash", 0) or 0) + sum(x["value_krw"] for x in p.get("positions", []))
        L.append(f"[{lbl} 총 {round(toss_tot):,}원, 현금 {round(p.get('cash',0)):,}원, "
                 f"오늘 {p.get('daily_pnl_pct')}% / 전체 {p.get('total_pnl_pct')}%]")
        for x in sorted(p.get("positions", []), key=lambda z: -z["value_krw"])[:15]:
            L.append(f"  - {x['symbol']} {x['qty']:g}주 수익률 {x['pnl_pct']}% 평가 {round(x['value_krw']):,}원")
    except Exception:  # noqa: BLE001
        pass
    if not sp:
        try:
            k = kis_accounts()
            L.append(f"[한투 실계좌 총 {round(k.get('total',0)):,}원, 전체 {k.get('total_pnl_pct')}%]")
            for x in k.get("positions", []):
                L.append(f"  - [{x['account']}] {x['symbol']} {x['name']} {x['qty']:g}주 {x['pnl_pct']}%")
        except Exception:  # noqa: BLE001
            pass
    try:
        sc = screen()[:8]
        L.append("[추세 스크리너 상위(점수=최근 상승세 순위, 매수신호 아님)]")
        for r in sc:
            L.append(f"  - {r['symbol']} 점수 {r['score']}"
                     f"{' 정배열' if r['trend_aligned'] else ''} (1M {r['ret_1m']}% 3M {r['ret_3m']}%)")
    except Exception:  # noqa: BLE001
        pass
    try:
        bt = screen_backtest()
        if bt.get("ic") is not None:
            L.append(f"[★스크리너 신뢰도: {bt['grade']} (IC {bt['ic']}). 점수 높은 종목 1개월 뒤 "
                     f"평균 {bt['high_avg']}% vs 점수 낮은 종목 {bt['low_avg']}%. "
                     f"이 배당/인컴 ETF군은 점수 높을수록 오히려 덜 오르는 평균회귀 경향 → 점수 추격매수 부적합]")
    except Exception:  # noqa: BLE001
        pass
    if not sp:
        try:
            st = _r.get("paper:strategy")
            if st:
                sg = json.loads(st)
                rg = "위험회피(하락장 방어, 새틀 중단·코어 절반·현금↑)" if sg.get("regime") == "risk_off" else "정상(risk-on)"
                L.append(f"[모의 자동매매 전략현황] 코어-새틀라이트+MA50 추세추종. "
                         f"시장레짐={rg}. 코어(약85%) {sg.get('core')}, 새틀(약15%) {sg.get('sat')}. "
                         f"MA50 추세 꺾인 종목은 매도·현금화. 점수추격/단타는 검증상 손해라 안 씀.")
        except Exception:  # noqa: BLE001
            pass
        try:
            ex = exposure()
            if ex.get("top"):
                L.append("[실질 노출(ETF 룩스루): 상위5 " + str(ex["top5_pct"]) + "% 집중 — "
                         + ", ".join(f"{t['symbol']} {t['pct']}%" for t in ex["top"][:6])
                         + ". 여러 ETF여도 실제론 이 기업들에 노출(분산 착시 주의)]")
        except Exception:  # noqa: BLE001
            pass
    if sp:
        L.append("[분석 대상] 남편 토스 계좌(미국 ETF/개별주). 한투·모의는 본인 전용이라 제외. "
                 "토스는 모의환경 없는 실거래라 신중. 제안은 남편 보유 기준으로.")
    else:
        L.append("[계좌 매매제약] 연금저축=국내상장 ETF/ETN·비레버리지만(해외상장·개별주·레버리지 불가). "
                 "ISA중개형=국내상장 개별주/ETF(해외상장 직접불가, 순이익500만 비과세). 소수점=해외포함 자유. "
                 "토스(미국)=현금 거의 없어 신규매수 여력 적음. 교체는 같은 계좌 안에서만(계좌간 이동 시 연금 페널티·ISA혜택 손실).")
    return "\n".join(L)


@app.post("/api/chat")
def chat(body: dict):
    """투자 분석 챗봇 — 현재 데이터를 근거로 Groq가 답변(참고용, 결정은 사용자)."""
    msg = (body.get("message") or "").strip()
    if not msg:
        return {"reply": "질문을 입력해 주세요."}
    who = "spouse" if body.get("who") == "spouse" else "me"
    history = body.get("history")
    history = history[-6:] if isinstance(history, list) else []
    ctx = _chat_context(who)
    convo = ""
    for h in history:
        if not isinstance(h, dict):
            continue
        who = "사용자" if h.get("role") == "user" else "분석봇"
        convo += f"\n{who}: {str(h.get('content',''))[:500]}"
    prompt = (
        "너는 'stock-bot'의 한국어 투자 분석 어시스턴트다. 아래 [현재 데이터]만을 근거로 "
        "사용자의 실제 포트폴리오를 분석한다. 규칙:\n"
        "0) [주린이 모드·최우선] 사용자는 투자 완전초보다. 어려운 용어(MDD·샤프·IC·정배열·"
        "모멘텀·레버리지·리밸런싱·변동성·레짐 등)는 되도록 쓰지 말고, 꼭 필요하면 바로 옆 "
        "괄호에 쉬운 말 풀이를 붙여라(예: '최대낙폭(한때 가장 많이 빠진 폭)'). 중학생도 "
        "이해할 눈높이로, 비유와 '예: 100만원이면…' 숫자 예시를 곁들여 친절히 설명한다.\n"
        "1) 매수/매도 의견은 반드시 데이터 근거와 함께. 데이터에 없는 사실은 지어내지 말고 모른다고 한다.\n"
        "2) 스크리너 점수는 매수신호가 아님(신뢰도 참고). 계좌 매매제약을 꼭 반영.\n"
        "3) 단정/보장 금지. '참고이며 최종 결정과 책임은 본인'임을 의식하되 매 답변에 길게 면책 달지 말 것.\n"
        "4) 간결하게, 핵심 위주 불릿으로. 한국어.\n\n"
        f"[현재 데이터]\n{ctx}\n\n[대화]{convo}\n사용자: {msg}\n분석봇:")
    try:
        from bot.sentiment import _llm_chat
        reply = _llm_chat(prompt, max_tokens=900)
    except Exception as e:  # noqa: BLE001
        reply = None
        log.warning("chat 실패: %s", e)
    return {"reply": reply or "분석에 실패했어요. 잠시 후 다시 시도해 주세요."}


# ---------------- 정적 프론트(React 빌드) ----------------
_STATIC = Path(__file__).resolve().parent / "static"
if (_STATIC / "assets").exists():
    app.mount("/assets", StaticFiles(directory=_STATIC / "assets"), name="assets")
if (_STATIC / "index.html").exists():
    @app.get("/")
    def index():
        return FileResponse(_STATIC / "index.html")


@app.get("/sw.js")
def service_worker():
    """PWA 서비스워커 — 루트(/)에서 서빙해야 전체 앱 스코프를 제어할 수 있음."""
    return FileResponse(_STATIC / "assets" / "sw.js", media_type="application/javascript")
