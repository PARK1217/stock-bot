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
_AUTH_FREE = ("/api/login", "/api/health")


@app.middleware("http")
async def _auth(request: Request, call_next):
    if not _PW:                                    # 비번 미설정 = 인증 비활성(집망내)
        return await call_next(request)
    path = request.url.path
    if path in _AUTH_FREE or path.startswith("/assets"):
        return await call_next(request)
    if request.cookies.get(_AUTH_COOKIE) == _AUTH_TOKEN:
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return HTMLResponse(_LOGIN_HTML, status_code=401)


@app.post("/api/login")
def login(body: dict):
    if _PW and body.get("password") == _PW:
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
        st = _r.get("paper:strategy")                    # 전략 현황(코어/새틀 선정)
        if st:
            strat = json.loads(st)
            out["strategy"] = strat
            core, sat = set(strat.get("core", [])), set(strat.get("sat", []))
            for p in out["positions"]:                    # 보유에 코어/새틀/이탈 태그
                p["bucket"] = ("core" if p["symbol"] in core else
                               "sat" if p["symbol"] in sat else "exit")
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)
    with SessionLocal() as s:
        snaps = s.query(PaperSnapshot).order_by(PaperSnapshot.id.desc()).limit(90).all()
        out["history"] = [{"ts": str(x.ts), "total": x.total_eval}
                          for x in reversed(snaps)]
    if out["total"]:
        out["ret_pct"] = round((out["total"] / 500_000_000 - 1) * 100, 2)  # 초기 5억
    _cache_set("web:paper", out, 30)
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


@app.get("/api/paper/trades")
def paper_trades(page: int = 0, size: int = 8):
    """모의 거래내역 — KIS 실제 체결원장(ccnl) 기반. 매수·매도 전부(자동+수동). 페이징."""
    page = max(0, page); size = min(max(size, 1), 50)
    if (c := _cache_get(f"web:ptr:{page}:{size}")):
        return c
    fx = 1540.0
    ledger = []
    try:
        from bot.brokers.kis import KISBroker
        from bot.brokers.toss import TossBroker
        fx = TossBroker().usdkrw() or 1540.0
        ledger = KISBroker(paper=True).overseas_orders(
            (datetime.now() - timedelta(days=10)).strftime("%Y%m%d"),
            datetime.now().strftime("%Y%m%d"))
    except Exception:  # noqa: BLE001
        pass
    ledger.sort(key=lambda r: r["dt"], reverse=True)        # 최신순
    items = []
    for r in ledger:
        ccld, ordq = r["ccld_qty"], r["ord_qty"]
        items.append({
            "ts": _fmt_dt(r["dt"]), "symbol": r["symbol"], "side": r["side"],
            "qty": ordq, "filled": ccld, "market": "US",
            "status": ("체결" if ccld >= ordq > 0 else
                       "부분체결" if ccld > 0 else "미체결"),
            "fill_price": round(r["price"], 2) if ccld > 0 else None,
            "amount_krw": round(r["amt"] * fx) if ccld > 0 else None})
    total = len(items)
    turnover = round(sum((r["amt"] or 0) for r in ledger) * fx)  # 매수+매도 체결 ₩
    out = {"items": items[page * size:(page + 1) * size], "page": page, "size": size,
           "total": total, "pages": (total + size - 1) // size if total else 0,
           "turnover_krw": turnover}
    _cache_set(f"web:ptr:{page}:{size}", out, 20)
    return out


@app.get("/api/snapshots")
def snapshots(limit: int = 60):
    with SessionLocal() as s:
        rows = s.query(DailySnapshot).order_by(DailySnapshot.id.desc()).limit(limit).all()
        return [{"ts": str(x.ts), "cash": x.cash, "total_eval": x.total_eval}
                for x in reversed(rows)]


def _chat_context() -> str:
    """챗봇 근거 데이터 — 현재 보유·스크리너·신뢰도·예측·계좌제약 요약."""
    L = []
    try:
        p = portfolio()
        toss_tot = (p.get("cash", 0) or 0) + sum(x["value_krw"] for x in p.get("positions", []))
        L.append(f"[토스 실계좌(미국) 총 {round(toss_tot):,}원, 현금 {round(p.get('cash',0)):,}원, "
                 f"오늘 {p.get('daily_pnl_pct')}% / 전체 {p.get('total_pnl_pct')}%]")
        for x in sorted(p.get("positions", []), key=lambda z: -z["value_krw"])[:15]:
            L.append(f"  - {x['symbol']} {x['qty']:g}주 수익률 {x['pnl_pct']}% 평가 {round(x['value_krw']):,}원")
    except Exception:  # noqa: BLE001
        pass
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
    try:
        st = _r.get("paper:strategy")
        if st:
            sg = json.loads(st)
            rg = "위험회피(하락장 방어, 새틀 중단·코어 절반·현금↑)" if sg.get("regime") == "risk_off" else "정상(risk-on)"
            L.append(f"[모의 자동매매 전략현황] 코어-새틀라이트+MA50 추세추종. "
                     f"시장레짐={rg}. 코어(70%) {sg.get('core')}, 새틀(30%) {sg.get('sat')}. "
                     f"MA50 추세 꺾인 종목은 매도·현금화. 점수추격/단타는 검증상 손해라 안 씀.")
    except Exception:  # noqa: BLE001
        pass
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
    history = body.get("history") or []
    ctx = _chat_context()
    convo = ""
    for h in history[-6:]:
        who = "사용자" if h.get("role") == "user" else "분석봇"
        convo += f"\n{who}: {h.get('content','')}"
    prompt = (
        "너는 'stock-bot'의 한국어 투자 분석 어시스턴트다. 아래 [현재 데이터]만을 근거로 "
        "사용자의 실제 포트폴리오를 분석한다. 규칙:\n"
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
