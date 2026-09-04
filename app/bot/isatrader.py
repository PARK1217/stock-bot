"""ISA 실계좌 자동매매 — 'B 균형형 고정배분 + 추세방어' (소액 검증 단계).

papertrader(모의)와 다른 점:
- 실돈(약 43만): 종목선택 없이 **고정 목표비중 5종**(백테스트 2023-06~2026-08:
  연환산 +24% · MDD -10.3% · 상승장 과대편향 주의) + 규칙만 자동화.
- **알림 후 자동**: 주문 직전 디스코드로 예고 → DELAY_MIN 대기(취소 가능) → 실행.
- **킬스위치**: redis isa:auto != "on" 이면 아무것도 안 함(기본 OFF).
- 가드: 매수 1회 상한 · 일일 주문수 상한 · ISA 계좌만 접근 · 전 주문 OrderLog+알림.

규칙(모의 검증 철학 이식):
① 밴드 리밸런싱 — 목표비중 대비 ±25%(상대) 이탈 시만 매매(회전 억제)
② MA50 추세방어 — 주식·금 슬리브가 MA50 아래면 목표 0 → 단기채로 피난, 회복 시 복귀
③ 낙폭 가드 — 계좌 고점대비 -10% 도달 시 위험자산 목표 절반(히스테리시스 -5% 회복까지)
④ 이슈 틸트(보조) — 구성종목(룩스루 프록시) 뉴스감성이 강한 악재면 추세방어를 가속.
   단독 매매근거 금지(방향적중 50%대 — 뉴스는 선반영 많음).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime

import redis

from bot.brokers.base import Side
from bot.brokers.kis import KISBroker
from bot.config import settings
from bot.notify import notify
from bot.storage.db import SessionLocal
from bot.storage.models import OrderLog

log = logging.getLogger(__name__)
_r = redis.from_url(settings.redis_url)

# ---------------- 설정 ----------------
# B 균형형 목표비중(현금 5% 버퍼 별도). 전부 국내상장·비레버리지 = ISA 가능.
TARGETS: dict[str, dict] = {
    # lt=True: 장기 우상향 보유 종목 — 매도는 사용자 '승인' 후에만(자동매도 금지, 사용자 결정 2026-09-04)
    "360200": {"name": "ACE 미국S&P500",        "w": 0.26, "sleeve": "equity", "lt": True},
    "458730": {"name": "TIGER 미국배당다우존스", "w": 0.14, "sleeve": "equity", "lt": True},
    "310970": {"name": "TIGER MSCI Korea",      "w": 0.12, "sleeve": "equity", "lt": True},
    "132030": {"name": "KODEX 골드선물(H)",     "w": 0.17, "sleeve": "gold"},
    "157450": {"name": "TIGER 단기통안채",      "w": 0.26, "sleeve": "haven"},
}
CASH_BUF = 0.05          # 현금 버퍼(주문 잔돈·호가 여유)
BAND = 0.25              # 밴드 리밸런싱(목표 대비 상대 ±25%)
TREND_MA = 50            # 추세필터(모의 검증: MA50이 낙폭 방어)
DD_GUARD = -0.10         # 낙폭 가드 발동(-10%)
DD_RESUME = -0.05        # 가드 해제(고점대비 -5% 이내 회복)
DELAY_MIN = 10           # 알림 후 자동 실행까지 대기(분)
MAX_BUY_KRW = 150_000    # 매수 1회 상한(매도=방어라 무제한)
MAX_ORDERS_DAY = 10      # 일일 주문수 상한
NEWS_ACCEL = -0.25       # 이슈 틸트 이 값 미만 = 강한 악재 → 추세방어 가속
EXDIV_HOLD_DAYS = 3      # 배당락 D-N 이내면 트림성 매도 보류(분배금 수령 후 실행)

# 분배금 주는 ETF만(310970은 TR=분배 재투자, 금·단기채는 분배 없음 → 배당가드 불필요)
DIV_PAYERS = {"458730": "monthly",    # TIGER 미국배당다우존스 — 월배당
              "360200": "quarterly"}  # ACE 미국S&P500 — 분기(1·4·7·10월) 소액

# 이슈 틸트용 룩스루 프록시(대표 구성종목 — 정밀 지수구성 아님, 감성 근사용)
PROXY_HOLDINGS = {
    "360200": ["MSFT", "NVDA", "AAPL", "AMZN", "META"],       # S&P500 상위
    "458730": ["ABBV", "KO", "PEP", "CVX", "VZ"],             # 다우배당(SCHD류)
    "310970": ["005930", "000660"],                            # MSCI Korea 상위(삼전·하이닉스)
    "132030": [],                                              # 금 — 개별종목 없음
    "157450": [],                                              # 단기채 — 없음
}

# redis 키
K_AUTO = "isa:auto"          # "on"이어야 실주문(킬스위치, 기본 OFF)
K_CANCEL = "isa:cancel"      # 대기 중 취소 플래그
K_PENDING = "isa:pending"    # 대기 주문(대시보드 표시용)
K_PEAK = "isa:peak"          # 계좌 고점(낙폭 가드)
K_GUARD = "isa:ddguard"      # 낙폭 가드 발동 상태
K_STATUS = "isa:status"      # 대시보드 상태 캐시
K_ODAY = "isa:ocount:"       # +YYYYMMDD 일일 주문 카운터
K_APPROVE = "isa:approve"    # 승인 대기 매도 목록(json) — 장기보유(lt) 종목 매도는 동의 필요
# isa:ok:{code}(24h)=매도 승인됨 / isa:no:{code}(3일)=거절(재요청 억제) / isa:asked:{code}(1일)=알림 중복방지


def _kr_open() -> bool:
    n = datetime.now()
    t = n.hour * 60 + n.minute
    return n.weekday() < 5 and 9 * 60 <= t <= 15 * 60 + 20   # 지연실행 여유로 15:20 컷


def _ma_ok(closes: list[float]) -> bool:
    if len(closes) < TREND_MA + 1:
        return True                       # 데이터 부족 시 중립(방어는 낙폭가드가 커버)
    return closes[-1] > sum(closes[-TREND_MA:]) / TREND_MA


def _issue_tilt(code: str) -> float:
    """ETF 구성종목(프록시) 뉴스감성 평균 — 보조신호(가속페달). 4h 캐시."""
    syms = PROXY_HOLDINGS.get(code) or []
    if not syms:
        return 0.0
    ck = f"isa:tilt:{code}"
    c = _r.get(ck)
    if c is not None:
        try:
            return float(c)
        except (TypeError, ValueError):
            pass
    vals = []
    try:
        from bot.news import get_sentiment
        for s in syms:
            try:
                ns = get_sentiment(s, "KR" if s[:1].isdigit() else "US")
                vals.append(max(-1.0, min(1.0, ns.score * ns.confidence)))
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    tilt = sum(vals) / len(vals) if vals else 0.0
    _r.set(ck, tilt, ex=14400)
    return tilt


def _lookup_exdiv(code: str) -> str:
    """다음 분배락(기준일)을 웹검색+LLM으로 조회 → 'YYYY-MM-DD' 또는 ''.
    LLM 단독은 날짜 환각 위험 → 검색 스니펫을 근거로 주고 형식·범위(45일 내) 엄격 검증."""
    try:
        from bot.research import tavily_search
        from bot import chateval
        name = TARGETS[code]["name"]
        now = datetime.now()
        hits = tavily_search(f"{name} ETF 분배금 지급 기준일 배당락 {now.year}년 {now.month}월", 4)
        if not hits:
            return ""
        ctx = "\n".join(f"- {h.get('title', '')}: {(h.get('content') or '')[:200]}" for h in hits)
        res = chateval.llm_call(
            f"오늘은 {now.date()}다. 아래 검색결과에서 한국 상장 ETF '{name}'의 '다음' 분배금 "
            f"기준일(배당락 관련일)을 찾아라.\n{ctx}\n\n"
            "확실하면 날짜만 YYYY-MM-DD 형식으로, 불확실하면 NONE 이라고만 답하라.", max_tokens=30)
        import re as _re
        m = _re.search(r"\d{4}-\d{2}-\d{2}", (res.get("text") or ""))
        if not m:
            return ""
        d = datetime.strptime(m.group(0), "%Y-%m-%d").date()
        delta = (d - now.date()).days
        return m.group(0) if 0 <= delta <= 45 else ""    # 과거·45일 밖 = 신뢰 불가 → 폴백
    except Exception:  # noqa: BLE001
        return ""


def _exdiv_imminent(code: str) -> tuple[bool, str]:
    """배당락 임박 여부(D-{EXDIV_HOLD_DAYS} 이내). ①검색+LLM(3일 캐시) ②실패 시 월말 휴리스틱.
    반환 (임박여부, 근거문자열)."""
    kind = DIV_PAYERS.get(code)
    if not kind:
        return False, ""
    ck = f"isa:exdiv:{code}"
    cached = _r.get(ck)
    if cached is None:
        found = _lookup_exdiv(code)
        _r.set(ck, found, ex=3 * 86400)
    else:
        found = cached.decode()
    today = datetime.now().date()
    if found:
        try:
            delta = (datetime.strptime(found, "%Y-%m-%d").date() - today).days
            if 0 <= delta <= EXDIV_HOLD_DAYS:
                return True, f"{found}(D-{delta})"
            if delta > EXDIV_HOLD_DAYS:
                return False, ""
        except ValueError:
            pass
    # 휴리스틱 폴백: 월배당=매월 말, 분기=1·4·7·10월 말 → 말일 4일 이내면 임박 취급
    if kind == "quarterly" and today.month not in (1, 4, 7, 10):
        return False, ""
    import calendar
    last = calendar.monthrange(today.year, today.month)[1]
    if last - today.day <= 4:
        return True, f"월말분배 추정(말일 D-{last - today.day})"
    return False, ""


def _effective_targets(kis) -> tuple[dict[str, float], dict]:
    """추세·낙폭가드·이슈틸트 반영한 실효 목표비중 계산. 반환 (code→weight, 진단정보)."""
    info: dict = {"trend": {}, "tilt": {}, "dd": None, "guard": False}
    eff = {c: t["w"] for c, t in TARGETS.items()}

    # ① MA50 추세방어(+이슈 가속): 주식·금 슬리브만. 이탈분은 단기채로 이관.
    moved = 0.0
    for code, t in TARGETS.items():
        if t["sleeve"] == "haven":
            continue
        try:
            closes = [c["close"] for c in kis.get_candles(code, "1d", 120) if c["close"] > 0]
        except Exception:  # noqa: BLE001
            closes = []
        ok = _ma_ok(closes)
        tilt = _issue_tilt(code)
        info["trend"][code] = "위" if ok else "이탈"
        info["tilt"][code] = round(tilt, 2)
        # 이슈 가속: 강한 악재면 MA50 '근처'(+2% 이내)여도 이탈 취급 → 방어 빠르게
        if ok and tilt < NEWS_ACCEL and closes:
            ma = sum(closes[-TREND_MA:]) / TREND_MA if len(closes) >= TREND_MA else 0
            if ma and closes[-1] < ma * 1.02:
                ok = False
                info["trend"][code] = "이탈(이슈가속)"
        if not ok:
            moved += eff[code]
            eff[code] = 0.0
    eff["157450"] += moved

    # ② 낙폭 가드: 고점대비 -10% → 위험자산 목표 절반(회복 -5%까지 유지)
    bal = kis.get_balance()
    total = bal.cash + sum(p.market_value for p in bal.positions)
    peak = float(_r.get(K_PEAK) or 0)
    if total > peak:
        peak = total
        _r.set(K_PEAK, peak)
    dd = (total / peak - 1) if peak > 0 else 0.0
    info["dd"] = round(dd * 100, 2)
    guarded = _r.get(K_GUARD) == b"1"
    if dd <= DD_GUARD:
        guarded = True
    elif guarded and dd >= DD_RESUME:
        guarded = False
    _r.set(K_GUARD, "1" if guarded else "0")
    info["guard"] = guarded
    if guarded:
        for code, t in TARGETS.items():
            if t["sleeve"] != "haven":
                cut = eff[code] * 0.5
                eff[code] -= cut
                eff["157450"] += cut
    return eff, info


def _plan_orders(kis, eff: dict[str, float]) -> tuple[list[dict], dict]:
    """실효비중 → 밴드 리밸런싱 주문 플랜(매도 먼저). 반환 (orders, 계좌스냅)."""
    bal = kis.get_balance()
    held = {p.symbol: p for p in bal.positions}
    total = bal.cash + sum(p.market_value for p in bal.positions)
    invest_cap = total * (1 - CASH_BUF)
    prices, orders = {}, []
    for code in TARGETS:
        try:
            prices[code] = kis.get_price(code)
        except Exception:  # noqa: BLE001
            prices[code] = held[code].current_price if code in held else 0
    # 매도 먼저(매도대금으로 매수 — 국내는 당일 매수 사용 가능)
    sells, buys = [], []
    budget = bal.cash
    for code, w in eff.items():
        price = prices.get(code) or 0
        if price <= 0:
            continue
        pos = held.get(code)
        cur = pos.market_value if pos else 0.0
        tgt = invest_cap * w
        gap = tgt - cur
        if tgt <= 0 and pos and pos.qty > 0:                 # 목표 0(추세이탈) → 전량매도
            sells.append({"code": code, "side": "sell", "qty": int(pos.qty), "price": price,
                          "why": "추세이탈 피난"})
            budget += pos.qty * price
            continue
        if tgt <= 0 or abs(gap) < tgt * BAND:                # 밴드 내 → 유지
            continue
        if gap < 0 and pos:                                  # 초과 → 트림
            qty = min(int((-gap) // price), int(pos.qty))
            if qty > 0:
                sells.append({"code": code, "side": "sell", "qty": qty, "price": price,
                              "why": "비중초과 트림"})
                budget += qty * price
        elif gap > 0:                                        # 부족 → 매수(상한·예산 캡)
            want = min(gap, MAX_BUY_KRW)
            qty = int(want // price)
            # 소액계좌 정수주 보정: 목표가 1주 가격보다 작아도 60% 이상이면 1주 허용
            # (예: 43만 계좌에서 12% 슬리브=4.9만 < 1주 4.93만 → 보정 없으면 영영 매수불가)
            if qty == 0 and want >= price * 0.6:
                qty = 1
            buys.append({"code": code, "side": "buy", "qty": qty, "price": price,
                         "why": "비중부족 매수", "gap": gap})
    # 매수는 예산(현금+매도대금) 안에서 gap 큰 순으로
    buys.sort(key=lambda b: -b["gap"])
    for b in buys:
        qty = min(b["qty"], int(budget // b["price"])) if b["price"] > 0 else 0
        if qty > 0:
            b["qty"] = qty
            budget -= qty * b["price"]
            orders.append(b)
    orders = sells + [b for b in orders]
    for o in orders:
        o["name"] = TARGETS[o["code"]]["name"]
        o["krw"] = round(o["qty"] * o["price"])
    snap = {"total": round(total), "cash": round(bal.cash),
            "held": {c: {"qty": p.qty, "pnl": round(p.pnl_pct, 1)} for c, p in held.items()}}
    return orders, snap


def _fmt_orders(orders) -> str:
    return "\n".join(f"   · {o['side'].upper()} {o['name']}({o['code']}) {o['qty']}주 ≈{o['krw']:,}원 — {o['why']}"
                     for o in orders)


def run_isa(dry: bool = False) -> dict:
    """ISA 자동매매 1회 실행. dry=True면 주문 없이 플랜·알림 미리보기만."""
    kis = KISBroker(account=(settings.kis_main_cano, "01"), paper=False)   # ISA 전용
    eff, info = _effective_targets(kis)
    orders, snap = _plan_orders(kis, eff)
    # 배당가드 — 분배금 ETF의 '트림성' 매도는 배당락 임박(D-3)이면 보류(분배금 받고 다음 점검 때 실행).
    # 추세이탈 피난·낙폭 방어 매도는 배당보다 우선(0.n% 분배금보다 낙폭 방어가 큼) → 즉시.
    div_hold = []
    kept = []
    for o in orders:
        if o["side"] == "sell" and o["why"] == "비중초과 트림":
            imm, when = _exdiv_imminent(o["code"])
            if imm:
                div_hold.append(f"{o['name']} — 배당락 {when} 보유 유지")
                continue
        kept.append(o)
    orders = kept
    # 장기보유 승인가드 — 우상향 장기종목(lt)의 매도는 사용자 동의 후에만 실행(자동매도 금지).
    # 승인(isa:ok) 있으면 이번 런에 포함, 거절(isa:no)이면 3일간 조용히 보류, 아니면 동의 요청.
    need_approval, kept2 = [], []
    for o in orders:
        if o["side"] == "sell" and TARGETS.get(o["code"], {}).get("lt"):
            if _r.get(f"isa:ok:{o['code']}"):
                o["why"] += "(사용자 승인)"
                kept2.append(o)
                continue
            if _r.get(f"isa:no:{o['code']}"):
                continue
            need_approval.append({k: o[k] for k in ("code", "name", "side", "qty", "krw", "why")})
            continue
        kept2.append(o)
    orders = kept2
    if need_approval:
        _r.set(K_APPROVE, json.dumps(need_approval, ensure_ascii=False), ex=86400)
        if not dry:
            for o in need_approval:                       # 하루 1회만 알림(스팸 방지)
                nk = f"isa:asked:{o['code']}"
                if not _r.get(nk):
                    _r.set(nk, "1", ex=86400)
                    notify("🙋 [ISA] 장기보유 종목 매도 동의 요청 — 대시보드에서 승인/거절 해주세요\n"
                           f"   · {o['side'].upper()} {o['name']} {o['qty']}주 ≈{o['krw']:,}원 ({o['why']})\n"
                           "   (승인 전까지 매도하지 않아요. 승인 시 다음 점검 때 실행)")
    else:
        _r.delete(K_APPROVE)

    auto_on = (_r.get(K_AUTO) or b"").decode() == "on"
    result = {"dry": dry, "auto": auto_on, "eff": {k: round(v, 3) for k, v in eff.items()},
              "info": info, "snap": snap, "div_hold": div_hold, "need_approval": need_approval,
              "orders": [{k: o[k] for k in ("code", "name", "side", "qty", "krw", "why")} for o in orders],
              "executed": []}
    if div_hold:
        log.info("ISA 배당가드: %s", "; ".join(div_hold))

    # 상태 캐시(대시보드)
    _r.set(K_STATUS, json.dumps({**result, "ts": str(datetime.now())[:16]},
                                ensure_ascii=False), ex=86400)
    if not orders:
        log.info("ISA: 밴드 내 — 매매 없음 (총 %s원, dd %s%%)", snap["total"], info["dd"])
        return result
    if dry:
        log.info("ISA[DRY] 주문플랜 %d건:\n%s", len(orders), _fmt_orders(orders))
        return result
    if not auto_on:
        notify("ℹ️ [ISA] 리밸런싱 신호가 있지만 자동매매가 꺼져 있어요(대시보드에서 ON 가능)\n"
               + _fmt_orders(orders))
        return result
    if not _kr_open():
        log.info("ISA: 장 마감 — 주문 보류")
        return result
    if _r.get(K_PENDING):                     # 중복 실행 락 — 대기 중이면 새 런 스킵(이중주문 방지)
        log.info("ISA: 이미 실행 대기 중인 주문 있음 — 스킵")
        result["executed"] = ["skipped: 대기주문 존재(중복방지)"]
        return result
    # 일일 주문수 가드
    dkey = K_ODAY + datetime.now().strftime("%Y%m%d")
    used = int(_r.get(dkey) or 0)
    if used + len(orders) > MAX_ORDERS_DAY:
        notify(f"⛔ [ISA] 일일 주문 한도({MAX_ORDERS_DAY}건) 초과 예상 — 오늘은 보류")
        return result

    # ---- 알림 후 자동: 예고 → DELAY_MIN 대기(취소 체크) → 실행 ----
    _r.delete(K_CANCEL)
    _r.set(K_PENDING, json.dumps({"orders": result["orders"],
                                  "exec_at": str(datetime.now())[:16]}, ensure_ascii=False),
           ex=DELAY_MIN * 60 + 120)
    notify(f"⏳ [ISA 자동매매] {DELAY_MIN}분 후 아래 주문을 실행해요 — 대시보드에서 취소 가능\n"
           + _fmt_orders(orders)
           + (("\n💰 " + " / ".join(div_hold)) if div_hold else "")
           + f"\n   (계좌 {snap['total']:,}원 · 고점대비 {info['dd']}%"
           + (" · 🛡️낙폭가드 발동중" if info["guard"] else "") + ")")
    waited = 0
    while waited < DELAY_MIN * 60:
        time.sleep(20)
        waited += 20
        if _r.get(K_CANCEL):
            _r.delete(K_PENDING)
            notify("🚫 [ISA] 사용자가 취소했어요 — 주문 미실행")
            result["executed"] = ["cancelled"]
            return result
    _r.delete(K_PENDING)
    if not _kr_open():
        notify("⌛ [ISA] 대기 중 장이 마감돼 주문을 보류했어요")
        return result

    # ---- 실행 ----
    with SessionLocal() as session:
        for o in orders:
            side = Side.SELL if o["side"] == "sell" else Side.BUY
            res = kis.place_order(o["code"], side, o["qty"])   # 국내 시장가(대형 ETF)
            time.sleep(0.4)
            _r.incr(dkey); _r.expire(dkey, 86400)
            mark = "✅" if res.ok else "❌"
            if res.ok and o["side"] == "sell" and TARGETS.get(o["code"], {}).get("lt"):
                _r.delete(f"isa:ok:{o['code']}")          # 승인은 1회용 — 실행 후 소모
            result["executed"].append(f"{mark} {o['side']} {o['code']} {o['qty']}: {res.message}")
            session.add(OrderLog(broker="kis-isa", mode="live", symbol=o["code"],
                                 side=o["side"], qty=o["qty"], ok=res.ok, price=o["price"],
                                 order_id=res.order_id, reason=o["why"],
                                 message=(res.message or "")[:250]))
        session.commit()
    notify("🤖 [ISA 자동매매] 실행 결과\n" + "\n".join("   " + x for x in result["executed"]))
    _r.set(K_STATUS, json.dumps({**result, "ts": str(datetime.now())[:16]},
                                ensure_ascii=False), ex=86400)
    return result
