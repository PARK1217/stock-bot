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
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse)
from fastapi.staticfiles import StaticFiles

from bot.accounts import account_registry, kr_data_account
from bot.brokers import get_broker
from bot.glossary import BEGINNER_RULE
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
# 데모(포트폴리오) 로그인 — 별도 토큰. 이 쿠키로는 /api가 합성데이터만 반환(실계좌 미도달).
_DEMO_PW = settings.demo_password
_DEMO_TOKEN = hashlib.sha256(f"stockbot::demo::{_DEMO_PW}".encode()).hexdigest()[:40] if _DEMO_PW else ""
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
_AUTH_FREE = ("/api/login", "/api/health", "/sw.js", "/assets/manifest.webmanifest",
              "/demo", "/api/demo-enter")            # 데모 전용 진입경로(비번없이 접근)
# 데모 랜딩 — /demo 로 접속하면 "합성데이터 체험판" 안내 + 원클릭 입장
_DEMO_HTML = """<!doctype html><html lang=ko><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>stock-bot 데모 체험</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:radial-gradient(120% 120% at 50% 0%,#141824 0%,#0d0f15 60%);color:#e6e9ef;
font-family:system-ui,-apple-system,sans-serif;padding:20px}
.box{background:#161a22;border:1px solid #2a2f3a;border-radius:18px;padding:34px 30px;width:340px;text-align:center;
box-shadow:0 18px 50px -18px rgba(0,0,0,.7)}
h1{font-size:22px;margin:0 0 6px}.tag{display:inline-block;background:#1f2a44;color:#7fb0ff;font-size:11px;
font-weight:700;border-radius:20px;padding:4px 11px;margin-bottom:16px}
p{font-size:13.5px;line-height:1.65;color:#aab2c0;margin:0 0 20px}
b{color:#e6e9ef}button{width:100%;background:#4c8dff;color:#fff;border:0;border-radius:10px;
padding:13px;font-size:15px;font-weight:700;cursor:pointer;transition:.15s}
button:hover{background:#3d7bf0}button:active{transform:scale(.98)}
.hint{margin:16px 0 0;font-size:12px;color:#7b8494}code{background:#0d1017;border:1px solid #2a2f3a;
color:#ffd454;border-radius:6px;padding:2px 8px;font-size:12.5px;font-weight:700}</style>
<div class=box>
  <div class=tag>PORTFOLIO DEMO</div>
  <h1>📈 stock-bot 체험</h1>
  <p>실제 대시보드를 <b>합성(가짜) 데이터</b>로 둘러보는 체험판입니다.<br>
  실계좌·자산·손익 정보는 <b>전혀 포함되지 않습니다.</b></p>
  <button onclick="location.href='/api/demo-enter'">데모 대시보드 입장 &rarr;</button>
  <p class=hint>🔑 직접 로그인하려면 비밀번호 <code>demo</code></p>
</div>
</html>"""


@app.middleware("http")
async def _auth(request: Request, call_next):
    if not _PW:                                    # 비번 미설정 = 인증 비활성(집망내)
        return await call_next(request)
    path = request.url.path
    if path in _AUTH_FREE or path.startswith("/assets/"):
        return await call_next(request)
    if hmac.compare_digest(request.cookies.get(_AUTH_COOKIE) or "", _AUTH_TOKEN):
        return await call_next(request)
    if _DEMO_TOKEN and hmac.compare_digest(request.cookies.get(_AUTH_COOKIE) or "", _DEMO_TOKEN):
        # 데모 세션: /api는 합성데이터로 가로채(실 KIS/토스/DB 미도달), HTML·정적은 실제 그대로 서빙
        if path.startswith("/api/"):
            from bot.demo import demo_api
            body = None
            if request.method not in ("GET", "HEAD"):
                try:
                    body = await request.json()
                except Exception:  # noqa: BLE001
                    body = {}
            return JSONResponse(demo_api(request.method, path,
                                         dict(request.query_params), body) or {})
        if path in ("/", "/rag"):        # 데모 화면: 나/남편 토글 숨김(개인기능 비노출)
            fname = "index.html" if path == "/" else "rag.html"
            try:
                html = (_STATIC / fname).read_text(encoding="utf-8").replace(
                    "</head>",
                    "<style>.user-tog{display:none!important}</style></head>", 1)
                return HTMLResponse(html)
            except Exception:  # noqa: BLE001
                pass
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
    if _DEMO_PW and hmac.compare_digest(str(body.get("password") or ""), _DEMO_PW):
        resp = JSONResponse({"ok": True, "demo": True})    # 데모: 합성데이터 세션
        resp.set_cookie(_AUTH_COOKIE, _DEMO_TOKEN, max_age=60 * 60 * 24 * 30,
                        httponly=True, samesite="lax")
        return resp
    return JSONResponse({"ok": False}, status_code=401)


@app.get("/demo")
def demo_landing():
    """데모 전용 진입 랜딩(비번없이 접근). 합성데이터 체험 안내 + 원클릭 입장."""
    return HTMLResponse(_DEMO_HTML)


@app.get("/api/demo-enter")
def demo_enter():
    """원클릭 데모 입장 — 데모 쿠키 세팅 후 대시보드로. (합성데이터만, 실계좌 격리)"""
    if not _DEMO_TOKEN:
        return RedirectResponse("/", status_code=302)
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(_AUTH_COOKIE, _DEMO_TOKEN, max_age=60 * 60 * 24 * 30,
                    httponly=True, samesite="lax")
    return resp


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
                    "total_krw": 0, "fx": settings.fx_fallback, "broker": "toss"}
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
    if not syms:
        return []
    from bot import news
    from concurrent.futures import ThreadPoolExecutor

    def one(s):
        c = _cache_get(f"web:news:{s}")
        if c is None:
            try:
                ns = news.get_sentiment(s, "US")
                c = {"symbol": s, "score": round(ns.score, 2),
                     "polarity": news.polarity(ns.score),
                     "summary": ns.summary, "sources": ns.sources}
            except Exception:  # noqa: BLE001
                c = {"symbol": s, "score": 0, "polarity": "중립",
                     "summary": "(조회 오류)", "sources": 0}
            _cache_set(f"web:news:{s}", c, 4 * 3600)
        return c

    # 종목별 요약은 서로 독립 → 병렬(순차 10종목 = 수분 지연 방지). map은 입력순서 보존.
    with ThreadPoolExecutor(max_workers=min(3, len(syms))) as ex:
        return list(ex.map(one, syms))


@app.get("/api/issues")
def issues(days: int = 14):
    """뉴스 감성 이슈 히스토리 — 장중 배치 적재분. 날짜·종목별 최신 1건(극성 포함).
    RAG 평가 '이슈 히스토리'에서 날짜별로 정리해 표시."""
    from datetime import date as _date, timedelta
    from bot.storage.models import NewsIssue
    cutoff = (_date.today() - timedelta(days=max(1, days))).isoformat()
    with SessionLocal() as s:
        rows = (s.query(NewsIssue)
                .filter(NewsIssue.date >= cutoff)
                .order_by(NewsIssue.ts.desc()).limit(3000).all())
    seen, out = set(), []                       # (날짜,종목) 최신 1건만
    for r in rows:
        k = (r.date, r.symbol)
        if k in seen:
            continue
        seen.add(k)
        out.append({"date": r.date, "session": r.session, "symbol": r.symbol,
                    "market": r.market, "score": r.score, "polarity": r.polarity,
                    "summary": r.summary, "sources": r.sources,
                    "ret_1d": r.ret_1d, "ret_5d": r.ret_5d, "impact": r.impact,
                    "rvol": r.rvol, "ts": r.ts.isoformat() if r.ts else ""})
    return out


@app.get("/api/volume")
def volume(symbols: str = Query(default="")):
    """종목별 상대거래량(RVOL=오늘/평균). 보유종목 거래량 급증 표시용. 30분 캐시."""
    from bot.screener import rel_volume
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:40]
    if not syms:
        return {}
    ck = "web:rvol:" + ",".join(sorted(syms))
    if (c := _cache_get(ck)) is not None:
        return c
    from bot.brokers.kis import KISBroker
    out, kis = {}, None
    for s in syms:
        try:
            if s.isdigit():
                kis = kis or KISBroker(account=kr_data_account(), paper=False)
                cs = kis.get_candles(s, "1d", 25)
            else:
                cs = get_broker().get_candles(s, "1d", 25)
            rv = rel_volume(cs)
            if rv is not None:
                out[s] = rv
        except Exception:  # noqa: BLE001
            pass
    _cache_set(ck, out, 1800)
    return out


@app.get("/api/kis")
def kis_accounts():
    """한투 실계좌 통합(ISA·연금=국내, 소수점=해외). 캐시 60초."""
    if c := _cache_get("web:kis"):
        return c
    from bot.brokers.kis import KISBroker
    from bot.brokers.toss import TossBroker
    fx = TossBroker().usdkrw() or settings.fx_fallback
    accts = [(a.cano, a.prod, a.label, a.market) for a in account_registry()]
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
                    _kb = KISBroker(account=kr_data_account(), paper=False)
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
        k = KISBroker(account=kr_data_account(), paper=False)
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
    from bot.etf import lookthrough, top_constituents, stock_name
    hold = []                                        # (symbol, value_krw)
    names: dict[str, str] = {}                       # 보유 종목명(코드→이름)
    partial = False
    try:
        for x in portfolio("").get("positions", []):
            hold.append((x["symbol"], x["value_krw"]))
            names[x["symbol"]] = x.get("name") or ""
    except Exception:  # noqa: BLE001
        partial = True
    try:
        for x in kis_accounts().get("positions", []):
            hold.append((x["symbol"], x["value_krw"]))
            names[x["symbol"]] = x.get("name") or ""
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
           "top": [{"symbol": s, "name": names.get(s) or stock_name(s),
                    "krw": round(v), "pct": round(v / total * 100, 1)}
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
def screen(refresh: bool = False, market: str = "us", kind: str = "etf"):
    market = "kr" if market.lower() == "kr" else "us"
    kind = "single" if kind.lower() == "single" else "etf"   # etf(기본) / single(개별주)
    key = f"web:screen:{market}:{kind}"
    if not refresh and (c := _cache_get(key)):
        return c
    from bot.screener import (screen as run_screen, DEFAULT_WATCHLIST,
                              KR_WATCHLIST, SINGLE_US, SINGLE_KR)
    if market == "kr":
        from bot.brokers.kis import KISBroker
        b = KISBroker(account=kr_data_account(), paper=False)   # KR 시세용
        wl = SINGLE_KR if kind == "single" else KR_WATCHLIST
    else:
        b = get_broker()
        wl = SINGLE_US if kind == "single" else DEFAULT_WATCHLIST
    candles = {}
    for s in wl:
        try:
            candles[s] = b.get_candles(s, "1d", 200)
        except Exception:  # noqa: BLE001
            pass
    results = run_screen(candles)
    preds = {}                          # 종목별 최신 예측(미래 기대수익·상승확률)
    try:
        with SessionLocal() as s:
            syms = [r.symbol for r in results]
            for p in (s.query(Prediction).filter(Prediction.symbol.in_(syms))
                      .order_by(Prediction.id.desc())):
                if p.symbol not in preds:
                    preds[p.symbol] = {"exp_return": p.exp_return, "prob_up": p.prob_up,
                                       "horizon": p.horizon_days}
    except Exception:  # noqa: BLE001
        pass
    out = [{
        "symbol": r.symbol, "score": round(r.score, 1), "last": r.last,
        "ret_1m": r.ret_1m, "ret_3m": r.ret_3m, "above_sma200": r.above_sma200,
        "trend_aligned": r.trend_aligned, "vol_20d": round(r.vol_20d, 2),
        "rvol": r.rvol, "forecast": preds.get(r.symbol),
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
def predictions(limit: int = 600):
    with SessionLocal() as s:
        rows = s.query(Prediction).order_by(Prediction.id.desc()).limit(limit).all()
        return [{
            "id": p.id, "made_at": str(p.made_at), "symbol": p.symbol,
            "horizon_days": p.horizon_days, "base_price": p.base_price,
            "prob_up": p.prob_up, "exp_return": p.exp_return,
            "status": p.status, "actual_return": p.actual_return,
            "dir_hit": p.dir_hit, "band_hit": p.band_hit,
            "miss_reason": p.miss_reason, "winrate": p.backtest_winrate,
        } for p in rows]


@app.get("/api/accuracy")
def accuracy():
    if (c := _cache_get("web:accuracy")) is not None:   # 분 단위로 안 바뀜 → 60초 캐시(매 60초·챗봇마다 풀스캔 방지)
        return c
    with SessionLocal() as s:
        ev = s.query(Prediction.dir_hit, Prediction.band_hit, Prediction.horizon_days
                     ).filter(Prediction.status == "evaluated").all()
    if not ev:
        out = {"evaluated": 0, "dir_acc": None, "band_acc": None, "by_horizon": []}
        _cache_set("web:accuracy", out, 60)
        return out
    n = len(ev)
    by_h = []
    for hz in sorted({p.horizon_days for p in ev}):     # 7·14·21영업일 각각
        g = [p for p in ev if p.horizon_days == hz]
        by_h.append({"horizon": hz, "evaluated": len(g),
                     "dir_acc": round(sum(1 for p in g if p.dir_hit) / len(g), 3),
                     "band_acc": round(sum(1 for p in g if p.band_hit) / len(g), 3)})
    out = {
        "evaluated": n,
        "dir_acc": round(sum(1 for p in ev if p.dir_hit) / n, 3),
        "band_acc": round(sum(1 for p in ev if p.band_hit) / n, 3),
        "by_horizon": by_h,
    }
    _cache_set("web:accuracy", out, 60)
    return out


@app.get("/api/paper")
def paper():
    """모의 자동매매 계좌(전략 검증) — 잔고·보유·성적표."""
    if c := _cache_get("web:paper"):
        return c
    from bot.brokers.kis import KISBroker
    from bot.brokers.toss import TossBroker
    out = {"cash": 0, "total": 0, "positions": [], "history": [], "ret_pct": None}
    fx = settings.fx_fallback
    # KR·US 잔고를 독립적으로 조회 — KIS 모의 국내(inquire-balance)가 간헐 500을 내도
    # US 보유까지 통째로 0이 되지 않게 분리(한쪽 실패해도 나머지는 표시).
    kr_pos, us_pos, errs = [], [], []
    kr_pnl = us_pnl = 0.0
    kis = None
    try:
        kis = KISBroker(paper=True)
        fx = TossBroker().usdkrw() or settings.fx_fallback
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
        total = settings.paper_initial_krw + kr_pnl + us_pnl    # 초기 5억 + 손익(통합증거금 이중계산 방지)
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
    out["initial"] = settings.paper_initial_krw               # 모의 초기자본(프론트 단일 출처)
    if out["total"]:
        out["ret_pct"] = round((out["total"] / settings.paper_initial_krw - 1) * 100, 2)
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
    out = {"names": N.resolved()}                  # 학습분 + 정적 한글 시드(시드 우선)
    _cache_set("web:names", out, 60)
    return out


@app.get("/api/realized")
def realized_view():
    """실현손익(매도 기준) 계좌별+전체 일별·월별. 평단가 이동평균 계산. 300초 캐시."""
    if (c := _cache_get("web:realized")):
        return c
    from bot import realized
    from bot.brokers.toss import TossBroker
    fx = TossBroker().usdkrw() or settings.fx_fallback
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
    for a in account_registry():
        if a.overseas:                       # 소수점=해외계좌 → 국내주문 API 대상 아님
            continue
        try:
            b = KISBroker(account=a.acct, paper=False)
            for o in b.domestic_orders(s, e):
                o["account"] = a.label
                rows.append(o)
        except Exception as ex:  # noqa: BLE001
            log.warning("kis orders %s 실패: %s", a.label, ex)
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
    fx = settings.fx_fallback
    rows = []                                            # (정렬키 dt, item)
    turnover = 0.0
    seen_us = set()                                      # ccnl에 잡힌 US (종목,방향,일자) — OrderLog 중복방지
    try:
        from bot.brokers.kis import KISBroker
        from bot.brokers.toss import TossBroker
        fx = TossBroker().usdkrw() or settings.fx_fallback
        for r in KISBroker(paper=True).overseas_orders(            # US (ccnl)
                (datetime.now() - timedelta(days=10)).strftime("%Y%m%d"),
                datetime.now().strftime("%Y%m%d")):
            ccld, ordq = r["ccld_qty"], r["ord_qty"]
            amt = round(r["amt"] * fx) if ccld > 0 else None
            if ccld > 0:
                turnover += (r["amt"] or 0) * fx          # 체결분만(KR과 기준 통일)
            seen_us.add((r["symbol"], r["side"], (r["dt"] or "")[:8]))
            rows.append((r["dt"], {
                "ts": _fmt_dt(r["dt"]), "symbol": r["symbol"], "side": r["side"],
                "qty": ordq, "filled": ccld, "market": "US",
                "status": ("체결" if ccld >= ordq > 0 else "부분체결" if ccld > 0 else "미체결"),
                "fill_price": round(r["price"], 2) if ccld > 0 else None, "amount_krw": amt}))
    except Exception:  # noqa: BLE001
        pass
    try:                                                          # US OrderLog 보완(ccnl 누락분: 모의 해외 체결조회 지연)
        with SessionLocal() as s:
            us = (s.query(OrderLog)
                  .filter(OrderLog.mode == "paper", OrderLog.ok.is_(True),
                          OrderLog.broker.like("%us%"))
                  .order_by(OrderLog.id.desc()).limit(200).all())
            for t in us:
                dt = (t.ts + timedelta(hours=9)).strftime("%Y%m%d%H%M%S")   # UTC→KST
                if (t.symbol, t.side.lower(), dt[:8]) in seen_us:           # ccnl에 이미 있음
                    continue
                amt = round((t.price or 0) * t.qty * fx) if t.price else None
                if amt:
                    turnover += amt
                rows.append((dt, {
                    "ts": _fmt_dt(dt), "symbol": t.symbol, "side": t.side,
                    "qty": t.qty, "filled": t.qty, "market": "US", "status": "체결",
                    "fill_price": (t.price or None), "amount_krw": amt}))
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
        L.append("[미국 추세 스크리너 상위(점수=최근 상승세 순위, 매수신호 아님)]")
        for r in sc:
            L.append(f"  - {r['symbol']} 점수 {r['score']}"
                     f"{' 정배열' if r['trend_aligned'] else ''} (1M {r['ret_1m']}% 3M {r['ret_3m']}%)")
    except Exception:  # noqa: BLE001
        pass
    try:
        sck = screen(market="kr")[:6]
        if sck:
            L.append("[국내(KR) 추세 스크리너 상위(점수=상승세, 매수신호 아님)]")
            for r in sck:
                L.append(f"  - {r['symbol']} 점수 {r['score']}"
                         f"{' 정배열' if r['trend_aligned'] else ''} (1M {r['ret_1m']}% 3M {r['ret_3m']}%)")
    except Exception:  # noqa: BLE001
        pass
    try:                                       # 모델 예측(미래 추정) — 종목별 최신
        from bot.storage.models import Prediction
        with SessionLocal() as s:
            seen = {}
            for pr in s.query(Prediction).order_by(Prediction.id.desc()).limit(150):
                seen.setdefault(pr.symbol, pr)
        if seen:
            L.append("[모델 예측(몬테카를로, 약 21일 앞 추정 — 단정 아님, '과거 추이'와 구분)]")
            for sym, pr in list(seen.items())[:20]:
                L.append(f"  - {sym}: 상승확률 {round((pr.prob_up or 0)*100)}%, "
                         f"기대수익 {round(pr.exp_return or 0,1)}%")
    except Exception:  # noqa: BLE001
        pass
    try:                                       # 예측 적중률(실측 신뢰도)
        ac = accuracy()
        if ac.get("evaluated"):
            L.append(f"[모델 예측 적중률(실측): 방향 {round(ac['dir_acc']*100)}% · "
                     f"범위 {round(ac['band_acc']*100)}% (표본 {ac['evaluated']}건). "
                     f"50%대면 동전던지기 수준이니 예측은 참고만]")
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
        L.append("[종목별 계좌 매수가능 — 아래 분류로 정확히 답할 것]\n"
                 "  · 국내상장 비레버리지 ETF(069500·360750·379800·458730·133690·161510·329200·273130·484790·210780 등): 연금·ISA·일반 모두 매수 가능\n"
                 "  · 국내상장 레버리지 ETF(122630 KODEX레버리지·233740 코스닥150레버리지): ISA·일반 가능, **연금은 레버리지라 불가**(국내상장이라 ISA는 됨)\n"
                 "  · 미국상장 전부(SCHD·JEPI·JEPQ·QQQI·SPYI·VIG·DGRO·O·NVDA·TSLA·SOXL·TQQQ 등): 소수점·토스·일반계좌만, 연금·ISA는 직접매수 불가")
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
    try:                                    # 질문에 나온 종목 온디맨드 리서치 주입(보유 무관, 최대 2종)
        from bot import research
        for t in research.resolve_query_tickers(msg)[:2]:
            blk = research.research_block(t["symbol"], t["market"])
            if blk:
                ctx += "\n\n" + blk
    except Exception as e:  # noqa: BLE001
        log.warning("리서치 주입 실패: %s", e)
    convo = ""
    for h in history:
        if not isinstance(h, dict):
            continue
        who = "사용자" if h.get("role") == "user" else "분석봇"
        convo += f"\n{who}: {str(h.get('content',''))[:500]}"
    prompt = (
        "너는 'stock-bot'의 한국어 투자 분석 어시스턴트다. 아래 [현재 데이터]를 근거로 답한다. "
        "[현재 데이터]에는 (a) 사용자 실제 보유·계좌와, (b) 사용자가 질문한 종목의 '[종목 리서치]' "
        "블록(추세·예측·뉴스·웹반응)이 함께 올 수 있다. [종목 리서치] 블록이 있으면 그 종목을 "
        "보유 여부와 무관하게 추세·이슈·전망을 분석해 준다('안 갖고 있어서 모른다'고 하지 말 것). 규칙:\n"
        f"0) [주린이 모드·최우선] {BEGINNER_RULE}\n"   # 용어 풀이 정책 단일 출처(glossary)
        "1) 매수/매도 의견은 반드시 데이터 근거와 함께. 데이터에 없는 사실은 지어내지 말고 모른다고 한다.\n"
        "2) 스크리너 점수는 매수신호가 아님(신뢰도 참고). 계좌 매매제약을 꼭 반영.\n"
        "3) 단정/보장 금지. '참고이며 최종 결정과 책임은 본인'임을 의식하되 매 답변에 길게 면책 달지 말 것.\n"
        "4) 과거와 미래를 반드시 구분: '과거에 N% 올랐다(=이미 지난 일)'와 '앞으로 모델 추정 N%/"
        "상승확률 N%(=예측)'를 헷갈리지 않게 따로 말한다.\n"
        "5) [완전 주린이용] 한 번에 핵심 2~3개만(쏟아내지 말 것). 따뜻하고 격려하는 말투로. "
        "답변 맨 끝에 반드시 '👉 쉽게 말하면: …' 한 줄 요약을 붙인다. 한국어 불릿.\n\n"
        f"[현재 데이터]\n{ctx}\n\n[대화]{convo}\n사용자: {msg}\n분석봇:")
    who_acct = "spouse" if body.get("who") == "spouse" else "me"
    # 동일 질문(+계좌+대화맥락)이면 LLM 재호출 없이 이전 답변 반환(토큰 절약). 시세변동 고려 30분.
    ckey = "chat:ans:" + hashlib.sha256(f"{who_acct}|{msg}|{convo}".encode()).hexdigest()[:32]
    if (cached := _r.get(ckey)):
        return {"reply": cached.decode(), "cached": True}
    reply = None
    try:
        from bot import chateval
        from bot.screener import DEFAULT_WATCHLIST, KR_WATCHLIST, SINGLE_US, SINGLE_KR
        from bot import names as N
        res = chateval.llm_call(prompt, max_tokens=900)
        reply = res.get("text")
        sys_prompt = prompt.split("\n\n[현재 데이터]\n")[0]   # 지침부 = 시스템 프롬프트(실제 데이터 마커로 분리)
        known = list(set(DEFAULT_WATCHLIST + KR_WATCHLIST + SINGLE_US + SINGLE_KR)
                     | set(N.all_learned().keys()))
        chateval.log_chat(msg, sys_prompt, ctx, reply, res.get("usage"), res.get("model"),
                          res.get("provider"), who_acct, known)
        if reply:
            _r.set(ckey, reply, ex=1800)            # 30분 캐시
    except Exception as e:  # noqa: BLE001
        log.warning("chat 실패: %s", e)
    return {"reply": reply or "분석에 실패했어요. 잠시 후 다시 시도해 주세요.", "cached": False}


@app.get("/api/chateval")
def chateval_view():
    """챗봇 콜 정확도 리포트(만기 콜 채점 포함). RAG평가 페이지 '챗봇 정확도'."""
    from bot import chateval
    return chateval.report()


@app.post("/api/chateval/log")
def chateval_log(body: dict):
    """챗봇 답변 기록 + 콜(매수/매도·상승/하락) 추출·저장. 프론트가 답변 받은 뒤 호출."""
    from bot import chateval
    from bot.screener import DEFAULT_WATCHLIST, KR_WATCHLIST, SINGLE_US, SINGLE_KR
    from bot import names as N
    known = list(set(DEFAULT_WATCHLIST + KR_WATCHLIST + SINGLE_US + SINGLE_KR)
                 | set(N.all_learned().keys()))
    who = "spouse" if body.get("who") == "spouse" else "me"
    n = chateval.log_interaction(str(body.get("q") or ""), str(body.get("a") or ""), who, known)
    return {"ok": True, "calls": n}


# ---------------- 정적 프론트(React 빌드) ----------------
_STATIC = Path(__file__).resolve().parent / "static"
if (_STATIC / "assets").exists():
    app.mount("/assets", StaticFiles(directory=_STATIC / "assets"), name="assets")
if (_STATIC / "index.html").exists():
    @app.get("/")
    def index():
        return FileResponse(_STATIC / "index.html")

if (_STATIC / "rag.html").exists():
    @app.get("/rag")
    def rag_page():                                # 예측·챗봇 검증(RAG 평가) 페이지(별도 URL)
        return FileResponse(_STATIC / "rag.html")


@app.get("/sw.js")
def service_worker():
    """PWA 서비스워커 — 루트(/)에서 서빙해야 전체 앱 스코프를 제어할 수 있음."""
    return FileResponse(_STATIC / "assets" / "sw.js", media_type="application/javascript")
