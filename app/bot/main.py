"""엔트리포인트 — 반자동(제안→승인) 자동매매.

사용법:
  python -m bot.main balance              잔고/환율 조회 (연결 점검)
  python -m bot.main propose              리밸런싱 '제안' 생성 (주문 안 함)
  python -m bot.main pending              승인 대기 제안 목록
  python -m bot.main approve <id|all>     제안 승인 후 실제 주문 실행
  python -m bot.main reject <id|all>      제안 거절
  python -m bot.main snapshot             일일 자산 스냅샷
  python -m bot.main run                  스케줄러 상주 (정해진 시각에 propose만)

안전장치:
  - 반자동: 봇은 제안만, 실행은 approve 했을 때만.
  - 토스 실거래 주문은 .env 의 TOSS_ALLOW_LIVE=true 일 때만 나간다.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import cast, Date

from bot.accounts import account_registry, kr_data_account
from bot.brokers import get_broker
from bot.brokers.base import Side
from bot.config import settings
from bot.notify import notify
from bot.storage.db import SessionLocal, init_db
from bot.storage.models import DailySnapshot, OrderLog, Proposal
from bot.strategies.dividend_core import DividendCoreStrategy
from bot.risk.rules import RiskManager

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")


def _build():
    return get_broker(), DividendCoreStrategy(), RiskManager()


def _mode() -> str:
    return "paper" if settings.is_paper else "live"


def _gather_prices(broker, strategy, balance) -> dict[str, float]:
    symbols = {i.symbol for i in strategy.basket} | {p.symbol for p in balance.positions}
    return {s: broker.get_price(s) for s in symbols}


# ---------- 조회 ----------
def cmd_balance() -> None:
    broker, _, _ = _build()
    fx = broker.usdkrw()
    bal = broker.get_balance()
    total_krw = bal.cash + sum(p.market_value_krw(fx) for p in bal.positions)
    print(f"[{broker.name}/{_mode()}]  USDKRW={fx:,.1f}")
    print(f"현금: {bal.cash:,.0f} KRW   총평가(원화환산): {total_krw:,.0f} KRW")
    for p in bal.positions:
        print(f"  {p.symbol:<8}{p.name[:14]:<16} {p.qty:g}주 "
              f"@{p.avg_price:,.2f} -> {p.current_price:,.2f} {p.currency} "
              f"({p.pnl_pct:+.1f}%)")


def cmd_balance_all() -> None:
    """KIS 3계좌(소수점·ISA·연금) 통합잔고. ⚠️소수점은 해외계좌라 별도 API 필요(추후)."""
    from bot.brokers.kis import KISBroker
    if settings.broker.lower() != "kis":
        print("balance-all은 BROKER=kis 전용. (토스는 balance)")
        return
    reg = {a.acct: a for a in account_registry()}   # 계좌 식별 단일 출처
    g_cash = g_eval = 0.0
    lines = [f"[KIS/{_mode()}] 3계좌 통합잔고"]
    for cano, prod in settings.kis_accounts:
        a = reg.get((cano, prod))
        label = a.label if a else f"{cano}-{prod}"
        if a and a.overseas:
            lines.append(f"  [{label} {cano}-{prod}] ⚠️해외계좌 — overseas 잔고 API 추후")
            continue
        try:
            b = KISBroker(account=(cano, prod)).get_balance()
        except Exception as e:  # noqa: BLE001
            lines.append(f"  [{label}] 조회실패: {e}")
            continue
        g_cash += b.cash
        g_eval += b.total_eval
        lines.append(f"  [{label} {cano}-{prod}] 현금 {b.cash:,.0f}  "
                     f"평가 {b.total_eval:,.0f}  {len(b.positions)}종")
        for p in b.positions:
            lines.append(f"      {p.symbol} {p.name[:14]} {p.qty:g}주 ({p.pnl_pct:+.1f}%)")
    lines.append(f"  ═ 국내계좌 합계: 현금 {g_cash:,.0f}  총평가 {g_eval:,.0f} KRW")
    msg = "\n".join(lines)
    print(msg)
    notify(msg)


def cmd_paper() -> None:
    """모의계좌 자동매매(예측+스크리너 종합) — KIS 모의에 실제 주문."""
    from bot.papertrader import run_paper
    r = run_paper()
    if r.get("error"):
        log.info("모의매매 스킵: %s", r["error"])
        return
    head = (f"🧪 모의 자동매매 · 총 {r['total']:,.0f}원 ({r['market']})\n"
            f"🛡️ 코어 {', '.join(r['core']) or '-'}  /  🚀 새틀 {', '.join(r['sat']) or '-'}")
    body = "\n".join(r["orders"]) if r["orders"] else "리밸런싱 변경 없음"
    notify(head + "\n" + body)
    print(head + "\n" + body)


# ---------- 제안 ----------
def cmd_propose() -> None:
    broker, strategy, risk = _build()
    fx = broker.usdkrw()
    bal = broker.get_balance()
    prices = _gather_prices(broker, strategy, bal)

    held_candles = {}
    if hasattr(broker, "get_candles"):
        for p in bal.positions:
            try:
                held_candles[p.symbol] = broker.get_candles(p.symbol, "1d", 80)
            except Exception:  # noqa: BLE001
                pass
    signals = risk.stop_loss_signals(bal, held_candles)   # 추세확인=분배침식 오발동 방지
    signals += strategy.generate(bal, prices, fx)

    with SessionLocal() as session:
        today = session.query(Proposal).filter(
            cast(Proposal.ts, Date) == datetime.now().date()).count()
        approved = risk.filter(signals, bal, prices, today, fx)
        if not approved:
            log.info("제안할 신호 없음 (밴드 이내 또는 차단)")
            notify("ℹ️ 오늘은 바꿀 거 없어요 — 지금 비중이 목표 범위 안이라 그대로 두면 돼요.")
            return

        lines = []
        for s in approved:
            p = Proposal(broker=broker.name, mode=_mode(), symbol=s.symbol,
                         side=s.side.value, qty=s.qty, currency=s.currency,
                         ref_price=prices.get(s.symbol, 0), reason=s.reason)
            session.add(p)
            session.flush()  # id 확보
            lines.append(f"#{p.id} {s.side.value.upper()} {s.symbol} "
                         f"{s.qty:g} @~{prices.get(s.symbol,0):,.2f}{s.currency} "
                         f"({s.reason})")
        session.commit()

    msg = ("🤖 오늘의 사고팔기 제안 ({}건) — 비중을 목표대로 맞추는 거예요\n{}\n\n"
           "👍 승인: `approve all`(전부) 또는 `approve <번호>`(하나만)"
           "\n👎 거절: `reject all`").format(len(lines), "\n".join(lines))
    notify(msg)
    print(msg)


def cmd_screen() -> None:
    """워치리스트 추세 스크리닝 — 추이 좋은 종목 랭킹 출력/통지."""
    from bot.screener import screen, DEFAULT_WATCHLIST
    broker, _, _ = _build()
    if not hasattr(broker, "get_candles"):
        print(f"{broker.name} 어댑터는 캔들 조회 미지원")
        return
    candles = {}
    for sym in DEFAULT_WATCHLIST:
        try:
            candles[sym] = broker.get_candles(sym, "1d", 200)
        except Exception as e:  # noqa: BLE001
            log.warning("%s 캔들 조회 실패: %s", sym, e)
    results = screen(candles)
    print("추세 스크리닝 (점수순):")
    for r in results:
        print("  " + r.line())
    top = results[:5]
    if top:
        msg = "🔎 요즘 흐름 좋은 종목 (참고용, 사라는 신호는 아니에요)\n" + "\n".join(
            f"{i+1}. {r.symbol} — 흐름점수 {r.score:.0f}점, 최근 3개월 "
            f"{('%+.1f%%' % r.ret_3m) if r.ret_3m is not None else '자료없음'}"
            for i, r in enumerate(top))
        notify(msg)


def cmd_news_warm(session: str = "") -> None:
    """뉴스 감성 이슈 정기 배치(장중 KR3·US3/일). 종목별 점수·극성·요약을
    ① news_issue DB 적재(날짜별 히스토리) ② 대시보드 캐시(web:news) 동시 갱신.
    session 미지정 시 현재 시각(KST)으로 자동 라벨."""
    import json
    import redis
    from datetime import datetime
    from bot import news
    from bot.screener import DEFAULT_WATCHLIST, KR_WATCHLIST
    from bot.storage.models import NewsIssue
    from bot.brokers.kis import KISBroker
    r = redis.from_url(settings.redis_url)
    now = datetime.now()                       # 컨테이너 TZ=Asia/Seoul
    if not session:
        h = now.hour
        session = ("KR-오전" if 8 <= h < 11 else "KR-점심" if 11 <= h < 14 else
                   "KR-오후" if 14 <= h < 18 else "US-초반" if (h >= 22 or h < 1) else
                   "US-중반" if 1 <= h < 3 else "US-후반")
    day = now.strftime("%Y-%m-%d")
    # 유니버스: US 실보유 + US 워치 + KR 워치
    us_broker = get_broker()
    us = []
    try:
        us = [p.symbol for p in us_broker.get_balance().positions]
    except Exception:  # noqa: BLE001
        pass
    us = list(dict.fromkeys(us + DEFAULT_WATCHLIST))
    universe = [(s, "US") for s in us] + [(s, "KR") for s in KR_WATCHLIST]
    try:
        kis = KISBroker(account=kr_data_account(), paper=False)   # KR 일봉 시세
    except Exception:  # noqa: BLE001
        kis = None

    def _recent_rets(sym, mkt):
        """최근 종가 → (1일%, 5일%, base1=전일종가, base5=5일전종가). 실패 시 None.
        base1/base5는 실시간 현재가로 재계산하기 위한 고정 기준종가."""
        try:
            br = us_broker if mkt == "US" else kis
            if br is None:
                return None, None, None, None
            cl = [c["close"] for c in br.get_candles(sym, "1d", 12) if c["close"] > 0]
            if len(cl) < 2:
                return None, None, None, None
            b1, b5 = cl[-2], (cl[-6] if len(cl) >= 6 else None)
            r1 = (cl[-1] / b1 - 1) * 100
            r5 = (cl[-1] / b5 - 1) * 100 if b5 else None
            return (round(r1, 2), (round(r5, 2) if r5 is not None else None),
                    round(b1, 4), (round(b5, 4) if b5 else None))
        except Exception:  # noqa: BLE001
            return None, None, None, None

    n = 0
    with SessionLocal() as sess:
        for sym, mkt in universe:
            try:
                ns = news.get_sentiment(sym, mkt)
                pol = news.polarity(ns.score)
                r1, r5, b1, b5 = _recent_rets(sym, mkt)
                imp = news.issue_impact(pol, r1)        # 영향=당일 움직임 기준(실시간 갱신과 동일)
                r.set(f"web:news:{sym}", json.dumps(
                    {"symbol": sym, "score": round(ns.score, 2), "polarity": pol,
                     "summary": ns.summary, "sources": ns.sources,
                     "ret_1d": r1, "ret_5d": r5, "impact": imp,
                     "base1": b1, "base5": b5}, ensure_ascii=False),
                    ex=5 * 3600)
                if ns.sources > 0:             # 기사 있는 것만 히스토리 적재
                    sess.add(NewsIssue(
                        date=day, session=session, symbol=sym, market=mkt,
                        score=round(ns.score, 3), polarity=pol,
                        confidence=round(ns.confidence, 3),
                        summary=(ns.summary or "")[:580], sources=ns.sources,
                        ret_1d=r1, ret_5d=r5, impact=imp))
                    n += 1
            except Exception:  # noqa: BLE001
                pass
        sess.commit()
    log.info("뉴스 이슈 배치(%s) %d종 적재", session, n)


def _technical_tilt(closes: list[float]) -> float:
    """스크리너 점수를 [-1,1] 드리프트 틸트로 변환."""
    from bot.screener import score_symbol
    r = score_symbol("", [{"close": c} for c in closes])
    if not r:
        return 0.0
    return max(-1.0, min(1.0, r.score / 25.0))


def cmd_forecast(symbol: str = "") -> None:
    """단일 종목 확률 예측 — 몬테카를로+백테스트+앙상블. DB 기록 후 통지."""
    from bot import news
    from bot.forecast import forecast_symbol
    if not symbol:
        print("사용법: forecast <symbol> [horizon_days]")
        return
    horizon = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 21
    broker, _, _ = _build()
    candles = broker.get_candles(symbol, "1d", 200)
    closes = [c["close"] for c in candles if c["close"] > 0]

    tech = _technical_tilt(closes)
    nws = news.tilt(symbol)
    fc = forecast_symbol(symbol, closes, horizon,
                         technical_tilt=tech, news_sentiment=nws)
    if fc is None:
        print(f"{symbol}: 데이터 부족(캔들 {len(closes)}봉)")
        return
    print(fc.report())

    with SessionLocal() as session:
        from bot.storage.models import Prediction
        session.add(Prediction(
            symbol=symbol, horizon_days=horizon, base_price=fc.last,
            prob_up=fc.prob_up, exp_return=fc.exp_return,
            p50=fc.band["p50"], p10=fc.band["p10"], p90=fc.band["p90"],
            backtest_winrate=fc.backtest_winrate))
        session.commit()
    wr = fc.backtest_winrate
    notify(f"🔮 {symbol} — 앞으로 {horizon}일 전망 (AI 추정치, 단정 아니에요)\n"
           f"   📈 오를 가능성 약 {fc.prob_up*100:.0f}%\n"
           f"   🎯 기대 수익 {fc.exp_return:+.1f}% 정도\n"
           f"   🚀 {horizon}일 안에 +5% 찍을 확률 {fc.target_touch['+5%']*100:.0f}%\n"
           f"   📚 과거 비슷한 신호가 맞았던 비율 "
           f"{('%.0f%%' % (wr*100)) if wr else '자료부족'}")


HORIZONS = (7, 14, 21)   # 예측·채점 기간(영업일). 단기(7)부터 빨리 검증되고 21은 중기.


def cmd_forecast_all() -> None:
    """워치리스트(US+KR) 전체 예측 생성·DB기록 (스케줄용). 종목당 7·14·21영업일 3건."""
    from bot import news
    from bot.forecast import forecast_symbol
    from bot.screener import DEFAULT_WATCHLIST, KR_WATCHLIST, SINGLE_US, SINGLE_KR
    from bot.storage.models import Prediction
    from bot.brokers.kis import KISBroker
    us_broker, _, _ = _build()
    kis = KISBroker(account=kr_data_account(), paper=False)   # KR 일봉용(실전 시세)
    made = [0]

    with SessionLocal() as session:
        # 재실행/카탈업 중복 방지 — 오늘 이미 만든 (종목,기간) 예측은 건너뜀
        today = datetime.now().date()
        existing = {(p.symbol, p.horizon_days)
                    for p in session.query(Prediction).filter(Prediction.status == "open").all()
                    if p.made_at and p.made_at.date() == today}

        def _predict(symbols, getc, market):
            for symbol in symbols:
                try:
                    closes = [c["close"] for c in getc(symbol, "1d", 200) if c["close"] > 0]
                    if len(closes) < 30:
                        continue
                    tilt = _technical_tilt(closes)
                    sent = news.tilt(symbol, market)
                    for hz in HORIZONS:             # 7·14·21영업일 동시 예측 → 단기부터 빠르게 검증
                        if (symbol, hz) in existing:           # 오늘 이미 예측함 → 중복 방지
                            continue
                        fc = forecast_symbol(symbol, closes, hz,
                                             technical_tilt=tilt, news_sentiment=sent)
                        if fc is None:
                            continue
                        existing.add((symbol, hz))             # 같은 실행 내 중복도 차단
                        session.add(Prediction(
                            symbol=symbol, horizon_days=hz, base_price=fc.last,
                            prob_up=fc.prob_up, exp_return=fc.exp_return,
                            p50=fc.band["p50"], p10=fc.band["p10"], p90=fc.band["p90"],
                            backtest_winrate=fc.backtest_winrate))
                        made[0] += 1
                except Exception as e:  # noqa: BLE001
                    log.warning("%s 예측 실패: %s", symbol, e)

        if hasattr(us_broker, "get_candles"):
            _predict(DEFAULT_WATCHLIST, us_broker.get_candles, "US")
            _predict(SINGLE_US, us_broker.get_candles, "US")     # 단일종목도 예측·검증
        _predict(KR_WATCHLIST, kis.get_candles, "KR")
        _predict(SINGLE_KR, kis.get_candles, "KR")               # 단일종목도 예측·검증
        session.commit()
    made = made[0]
    log.info("예측 생성 %d건", made)
    if made:
        notify(f"🔮 오늘의 종목별 전망 {made}개 업데이트했어요 "
               f"(7·14·21영업일 뒤 흐름 추정 — 단기부터 빨리 검증돼요!)")


def _bdays(start: datetime, end: datetime) -> int:
    """start~end 사이 영업일(월~금) 수. 공휴일은 무시(근사). 만기 판정용."""
    from datetime import timedelta
    d, e, n = start.date(), end.date(), 0
    while d < e:
        d += timedelta(days=1)
        if d.weekday() < 5:                  # 0=월 … 4=금
            n += 1
    return n


def _miss_reason(session, p) -> str:
    """빗나간 예측의 이유 추정 — 예측~채점 기간의 뉴스 이슈/큰 변동에서 근거를 찾는다.
    실제 움직임과 같은 방향의 강한 극성 이슈가 있으면 그걸로, 없고 변동만 크면 급등락,
    변동도 작으면 노이즈, 정말 아무것도 없으면 '이유 없음'."""
    from bot.storage.models import NewsIssue
    actual = p.actual_return or 0.0
    actual_up = actual > 0
    want = "긍정" if actual_up else "부정"        # 실제 움직임을 설명하는 극성
    try:
        rows = (session.query(NewsIssue)
                .filter(NewsIssue.symbol == p.symbol, NewsIssue.ts >= p.made_at)
                .all())
    except Exception:  # noqa: BLE001
        rows = []
    strong = [r for r in rows if r.polarity == want and abs(r.score or 0) >= 0.15
              and r.summary and "룩스루" not in r.summary]
    if strong:
        r0 = max(strong, key=lambda r: abs(r.score or 0))
        head = (r0.summary or "").strip().lstrip("- ").split("\n")[0][:70]
        return f"기간 중 {want} 이슈({r0.date}): {head}"
    if abs(actual) >= 10:
        return f"단기 급{'등' if actual_up else '락'}({actual:+.0f}%) — 뚜렷한 이슈는 미포착"
    if abs(actual) < 2:
        return "실제 변동이 작아 방향만 살짝 빗나감(노이즈 수준)"
    return "이유 없음 — 특이 이슈·큰 변동 없이 빗나감(모델 한계)"


def cmd_accuracy() -> None:
    """만기된 예측을 실측과 대조 → 자기예측 정확도(캘리브레이션) 산출.
    만기 = 예측일로부터 horizon '영업일'(주말 제외) 경과 시점.
    빗나간 예측은 _miss_reason 으로 이유까지 기록."""
    from bot.storage.models import Prediction
    from bot.brokers.kis import KISBroker
    broker, _, _ = _build()
    kis = KISBroker(account=kr_data_account(), paper=False)   # KR 종목 시세용
    now = datetime.now()
    with SessionLocal() as session:
        opens = session.query(Prediction).filter(Prediction.status == "open").all()
        for p in opens:
            if _bdays(p.made_at, now) < p.horizon_days:    # 영업일 기준 만기 미도래
                continue
            # KR(숫자코드)는 KIS, 그 외(US)는 기본 브로커로 시세 조회
            price = kis.get_price(p.symbol) if p.symbol[:1].isdigit() else broker.get_price(p.symbol)
            if price <= 0:
                continue
            p.actual_price = price
            p.actual_return = (price / p.base_price - 1) * 100
            p.dir_hit = (p.actual_return > 0) == (p.prob_up >= 0.5)
            p.band_hit = p.p10 <= price <= p.p90
            p.evaluated_at = datetime.now()
            p.status = "evaluated"
            p.miss_reason = None if p.dir_hit else _miss_reason(session, p)
        session.commit()

        ev = session.query(Prediction).filter(Prediction.status == "evaluated").all()
        if not ev:
            print("평가 완료된 예측이 아직 없습니다(만기 도래 후 산출).")
            return
        n = len(ev)
        dir_acc = sum(1 for p in ev if p.dir_hit) / n
        band_acc = sum(1 for p in ev if p.band_hit) / n
        print(f"자기예측 정확도 (표본 {n}건)")
        print(f"  방향 적중률: {dir_acc*100:.0f}%   범위(P10~P90) 적중률: {band_acc*100:.0f}%")
        print("  ※ 이 수치가 화면의 확률을 '얼마나 믿을지' 알려주는 진짜 정확도입니다.")


def cmd_pending() -> None:
    with SessionLocal() as session:
        rows = session.query(Proposal).filter(Proposal.status == "pending").all()
        if not rows:
            print("대기 중 제안 없음")
            return
        for p in rows:
            print(f"#{p.id} {p.side.upper()} {p.symbol} {p.qty:g} "
                  f"@~{p.ref_price:,.2f}{p.currency}  ({p.reason})  [{p.ts:%m-%d %H:%M}]")


# ---------- 승인/거절 ----------
def _select_pending(session, arg: str):
    q = session.query(Proposal).filter(Proposal.status == "pending")
    if arg != "all":
        ids = [int(x) for x in arg.split(",") if x.strip().isdigit()]
        q = q.filter(Proposal.id.in_(ids))
    return q.all()


def cmd_approve(arg: str = "all") -> None:
    broker, _, _ = _build()
    with SessionLocal() as session:
        rows = _select_pending(session, arg)
        if not rows:
            print("승인할 제안이 없습니다.")
            return
        for p in rows:
            side = Side.BUY if p.side == "buy" else Side.SELL
            res = broker.place_order(p.symbol, side, p.qty)  # 시장가
            p.status = "executed" if res.ok else "failed"
            p.order_id = res.order_id
            p.message = res.message[:250]
            session.add(OrderLog(
                broker=broker.name, mode=_mode(), symbol=p.symbol, side=p.side,
                qty=p.qty, price=p.ref_price, ok=res.ok, order_id=res.order_id,
                reason=p.reason, message=res.message[:250]))
            mark = "✅" if res.ok else "❌"
            notify(f"{mark} 주문 #{p.id} {p.side.upper()} {p.symbol} "
                   f"{p.qty:g} :: {res.message}")
        session.commit()
    print("승인 처리 완료.")


def cmd_reject(arg: str = "all") -> None:
    with SessionLocal() as session:
        rows = _select_pending(session, arg)
        for p in rows:
            p.status = "rejected"
        session.commit()
        print(f"{len(rows)}건 거절 처리.")


# ---------- 스냅샷/스케줄러 ----------
def cmd_snapshot() -> None:
    """일일 자산 스냅샷 — 토스 + 한투 계좌별 평가액을 기록(자산 흐름 그래프용)."""
    from bot.brokers.toss import TossBroker
    from bot.brokers.kis import KISBroker
    from bot.storage.models import AssetSnapshot
    toss = TossBroker()
    fx = toss.usdkrw() or settings.fx_fallback
    tb = toss.get_balance()
    toss_krw = tb.cash + sum(p.market_value_krw(fx) for p in tb.positions)

    isa = pension = frac = 0.0                       # 한투 계좌별 평가액
    for a in account_registry():
        try:
            b = KISBroker(account=a.acct, paper=False)
            bal = b.get_overseas_balance() if a.overseas else b.get_balance()
            v = (sum(p.market_value * fx for p in bal.positions) if a.overseas
                 else bal.total_eval)
            if a.key == "isa":
                isa = v
            elif a.key == "pension":
                pension = v
            else:
                frac = v
        except Exception as e:  # noqa: BLE001
            log.warning("snapshot KIS %s 실패: %s", a.label, e)
    kis_krw = isa + pension + frac
    total = toss_krw + kis_krw

    with SessionLocal() as session:
        session.add(DailySnapshot(cash=tb.cash, total_eval=toss_krw))   # 기존(토스) 유지
        session.add(AssetSnapshot(total_krw=total, toss_krw=toss_krw, kis_krw=kis_krw,
                                  isa_krw=isa, pension_krw=pension))
        session.commit()
    notify(f"💼 오늘 자산 요약 — 전체 {total:,.0f}원 (토스 {toss_krw:,.0f} + 한투 {kis_krw:,.0f})")


def _plain_backtest_msg(port: dict) -> str:
    """백테스트 숫자(MDD·샤프 등)를 주린이용 한글 안내로 풀어쓴다."""
    st, sp, sc = port.get("strategy") or {}, port.get("SPY") or {}, port.get("SCHD") or {}
    tot, mdd, shp = st.get("total"), st.get("mdd"), st.get("sharpe")
    if tot is None or shp is None:
        return "📊 전략 성적표: 이번엔 데이터가 부족해 계산을 건너뛰었어요. 다음에 다시 알려드릴게요."
    yrs = round((port.get("periods") or 0) / 252) or "수"
    grew = round(100 * (1 + tot / 100))
    L = [f"📊 우리 전략 성적표 — 지난 약 {yrs}년치로 모의실험 (실제 거래 아니에요)",
         "",
         "🤖 우리 전략이라면",
         f"   💰 {tot}% 불었어요  (100만원 넣었으면 약 {grew}만원)",
         f"   📉 가장 나빴을 땐 {mdd}%까지 빠졌다 회복  (= 견뎌야 할 최대 출렁임)",
         f"   ⚖️ 효율점수 {shp}점  (위험 대비 잘 번 정도 — 높을수록 좋고 1 넘으면 우수)",
         "",
         "📌 그냥 사두기만 했다면 (비교용)",
         f"   · 미국 대표지수 SPY : {sp.get('total')}% / 최악 {sp.get('mdd')}%",
         f"   · 배당주 모음 SCHD : {sc.get('total')}% / 최악 {sc.get('mdd')}%"]
    bits = []
    spm, spt = sp.get("mdd"), sp.get("total")
    if spm is not None and mdd is not None and mdd > spm:
        bits.append(f"폭락장에서 덜 빠졌어요({mdd}% vs 지수 {spm}%)")
    if spt is not None and tot is not None and tot < spt:
        bits.append(f"대신 수익은 지수보다 적어요({tot}% vs {spt}%)")
    L += ["", f"👉 한줄평: {' · '.join(bits) if bits else '지수와 비슷한 흐름이에요'}.",
          "   '많이 벌기'보다 '폭락 때 덜 잃기'에 무게 둔 안정형 전략이에요."]
    return "\n".join(L)


def cmd_backtest() -> None:
    """전략 백테스트 실행 — 코어+새틀 유니버스로 점수 모멘텀/평균회귀/벤치(+유의성 t값),
    돌파전략 fee 민감도. 결과 출력·디스코드 통지·redis 저장(대시보드 /api/backtest)."""
    import json as _json
    from bot.brokers.kis import KISBroker
    from bot.brokers.toss import TossBroker
    from bot.papertrader import CORE_KR, CORE_US, SAT_KR, SAT_US, _redis
    from bot.screener import backtest_strategy, backtest_breakout, backtest_portfolio
    kis, toss = KISBroker(paper=True), TossBroker()
    candles: dict[str, list[dict]] = {}
    for sym in CORE_KR + SAT_KR:
        try:
            candles[sym] = kis.get_candles(sym, "1d", 200)
        except Exception as e:  # noqa: BLE001
            log.warning("%s KR캔들 실패: %s", sym, e)
    # US: Tiingo 장기 일봉(다레짐, 분배조정=총수익) 우선, 없으면 토스 200봉 폴백.
    from bot.histdata import tiingo_candles
    for sym in CORE_US + SAT_US + ["SPY"]:                # SPY=레짐/벤치, SCHD는 코어에 포함
        cs = tiingo_candles(sym, start="2018-01-01")
        if not cs:
            try:
                cs = toss.get_candles(sym, "1d", 200)
            except Exception as e:  # noqa: BLE001
                log.warning("%s US캔들 실패: %s", sym, e)
                continue
        candles[sym] = cs
    src = "Tiingo다년치" if settings.tiingo_api_key else "토스200봉"
    print(f"[데이터소스] US={src}, 캔들 보유 {len(candles)}종목")
    # 가드: 장기데이터(>=1800봉) 4종목 미만이면 Tiingo 429/미설정 → 스킵(기존 결과 보존).
    if sum(1 for v in candles.values() if len(v) >= 1800) < 4:
        log.warning("장기데이터 부족(Tiingo 429/미설정?) → 백테스트 스킵, 기존 redis 결과 유지")
        notify("⚠️ 성적표 계산을 잠시 미뤘어요 (데이터 서버 일시 한도). 곧 다시 시도할게요.")
        return

    # ① 점수 백테스트(풀사이클: min_bars=1800으로 2018~ 장기종목만 → 다레짐 209구간)
    strat = backtest_strategy(candles, horizon=10, min_hist=40, min_bars=1800)
    print("[점수 백테스트] 상위(모멘텀) vs 하위(평균회귀) vs 벤치 — "
          f"n={strat.get('periods')} 종목={strat.get('included')} minlen={strat.get('minlen')}")
    print(f"  {strat}")

    # ② 실제 전략 포트폴리오 백테스트(MA50+코어/새틀+레짐) vs SPY/SCHD.
    #   다레짐(2020·2022) 포함 위해 장기이력(>=1500봉) 종목만 — 신생ETF가 끼면
    #   align-to-min으로 윈도우가 2024로 잘려 폭락장을 못 봄. core_w는 라이브와 일치.
    from bot.papertrader import CORE_W
    long_core = [s for s in CORE_US if len(candles.get(s, [])) >= 1800]
    long_sat = [s for s in SAT_US if len(candles.get(s, [])) >= 1800]
    port = backtest_portfolio(candles, long_core, long_sat,
                              bench=("SPY", "SCHD"), core_w=CORE_W)
    print(f"[전략 포트폴리오] MA50+코어/새틀+레짐 vs buy&hold (다레짐, core={long_core} "
          f"sat={long_sat} core_w={CORE_W})")
    print(f"  {port}")
    # A/B: 레짐 오버레이 끈 'MA50만' 변형 병행(라이브 미적용, 비교관찰용). 주간 누적 로그.
    port_ma = backtest_portfolio(candles, long_core, long_sat, bench=("SPY", "SCHD"),
                                 core_w=CORE_W, use_regime=False)
    print(f"[A/B 레짐] 풀전략 {port.get('strategy')} | MA만 {port_ma.get('strategy')}")
    abrec = {"ts": str(datetime.now())[:10],
             "full": port.get("strategy"), "ma_only": port_ma.get("strategy")}
    try:
        _redis.rpush("backtest:ab_log", _json.dumps(abrec))
        _redis.ltrim("backtest:ab_log", -52, -1)        # 최근 52주만
    except Exception:  # noqa: BLE001
        pass

    fees = [0.0005, 0.001, 0.002, 0.003]      # 5/10/20/30bp 왕복비용
    brk: dict[str, dict] = {}
    for sym, cs in candles.items():
        row = {f"{int(fee*1e4)}bp": backtest_breakout(cs, fee=fee).get("cum_ret")
               for fee in fees}
        base = backtest_breakout(cs, fee=0.0)
        row["buyhold"] = base.get("buyhold")
        row["trades"] = base.get("trades")
        brk[sym] = row
    print("[돌파 fee 민감도] 종목별 돌파누적(%) by fee  vs  buyhold(%)")
    for sym, row in brk.items():
        print(f"  {sym:<8} {row}")

    _redis.set("backtest:strategy",
               _json.dumps({"strategy": strat, "portfolio": port,
                            "portfolio_ma_only": port_ma, "breakout": brk,
                            "ts": str(datetime.now())[:16]}), ex=604800)
    notify(_plain_backtest_msg(port))


# 스케줄 정의 — (이름, 함수, 요일(cron), [(시,분)...]). add_job·catch-up이 공유.
_DOW = {"mon-fri": {0, 1, 2, 3, 4}, "tue-sat": {1, 2, 3, 4, 5}, "mon": {0}}
_SCHEDULE = [
    ("screen", lambda: cmd_screen(), "mon-fri", [(9, 10)]),
    ("propose", lambda: cmd_propose(), "mon-fri", [(10, 0)]),
    # 뉴스 감성 이슈 — 정규장 중 각 3회(KR/US). DB 적재 + 대시보드 캐시 갱신.
    ("news_kr", lambda: cmd_news_warm(), "mon-fri", [(9, 15), (12, 0), (15, 0)]),
    ("news_us_eve", lambda: cmd_news_warm(), "mon-fri", [(23, 0)]),          # US 개장(저녁)
    ("news_us_dawn", lambda: cmd_news_warm(), "tue-sat", [(1, 30), (4, 30)]),  # US 중·종반(새벽)
    ("snapshot_kr", lambda: cmd_snapshot(), "mon-fri", [(15, 40)]),   # KR 마감
    ("snapshot_us", lambda: cmd_snapshot(), "tue-sat", [(6, 10)]),    # US 마감(익일 새벽)
    ("forecast_all", lambda: cmd_forecast_all(), "mon-fri", [(9, 30)]),
    ("accuracy", lambda: cmd_accuracy(), "mon-fri", [(16, 0)]),
    ("backtest", lambda: cmd_backtest(), "mon", [(8, 0)]),
    ("paper", lambda: cmd_paper(), "mon-fri", [(9, 15), (12, 30), (15, 0), (23, 35)]),
    # US장 중·후반(전일 ET세션의 KST 새벽연장 23:30~05:00) — 중복방지 가드로 같은종목·방향 재발주 차단
    ("paper_us", lambda: cmd_paper(), "tue-sat", [(1, 30), (4, 0)]),
]


def _sched_redis():
    import redis
    return redis.from_url(settings.redis_url)


def _tracked(fn, name):
    """잡 실행 래퍼 — 끝나면 마지막 실행시각을 redis 기록(재시작 catch-up 판단용)."""
    def wrapped():
        try:
            fn()
        finally:
            try:
                _sched_redis().set(f"sched:lastrun:{name}",
                                   datetime.now().strftime("%Y%m%d%H%M"))
            except Exception:  # noqa: BLE001
                pass
    wrapped.__name__ = f"job_{name}"
    return wrapped


def _catchup() -> None:
    """봇 재시작 시 오늘 이미 지난 스케줄인데 그 이후 실행기록 없는 잡을 1회 보정 실행.
    최초 기동(기준키 없음)은 건너뜀=폭주 방지. paper는 시장게이팅으로 안전."""
    try:
        r = _sched_redis()
    except Exception:  # noqa: BLE001
        return
    now = datetime.now()
    nowmin = now.hour * 60 + now.minute
    for name, fn, dow, times in _SCHEDULE:
        if now.weekday() not in _DOW.get(dow, set()):
            continue
        past = [(h, m) for (h, m) in times if h * 60 + m <= nowmin]
        if not past:
            continue
        h, m = max(past, key=lambda t: t[0] * 60 + t[1])      # 오늘 지난 것 중 최신
        fire = now.replace(hour=h, minute=m, second=0, microsecond=0)
        try:
            last = r.get(f"sched:lastrun:{name}")
        except Exception:  # noqa: BLE001
            last = None
        if not last:                                          # 기준 없음 → 보정 안 함
            continue
        try:
            last_dt = datetime.strptime(last.decode(), "%Y%m%d%H%M")
        except (ValueError, AttributeError):
            continue
        if last_dt < fire:                                    # 최신 예정 이후 실행기록 없음=놓침
            log.info("⏱ catch-up: %s (놓친 %02d:%02d 보정)", name, h, m)
            try:
                fn()
                r.set(f"sched:lastrun:{name}", now.strftime("%Y%m%d%H%M"))
            except Exception as e:  # noqa: BLE001
                log.warning("catch-up %s 실패: %s", name, e)


def cmd_run() -> None:
    init_db()
    # misfire_grace_time=실행 시각 지나도 1h 내면 실행, coalesce=밀린 건 1회로 합침.
    sched = BlockingScheduler(timezone="Asia/Seoul",
                              job_defaults={"misfire_grace_time": 3600, "coalesce": True})
    for name, fn, dow, times in _SCHEDULE:
        for h, m in times:
            sched.add_job(_tracked(fn, name), "cron", day_of_week=dow, hour=h, minute=m,
                          id=f"{name}_{h:02d}{m:02d}", replace_existing=True)
    _catchup()                                  # 재시작 중 놓친 오늘 잡 따라잡기
    notify(f"🤖 stock-bot 스케줄러 시작 (반자동, {broker_label()})")
    log.info("scheduler started")
    sched.start()


def broker_label() -> str:
    return f"{settings.broker}/{_mode()}"


COMMANDS = {
    "balance": cmd_balance,
    "balance-all": cmd_balance_all,
    "paper": cmd_paper,
    "propose": cmd_propose,
    "screen": cmd_screen,
    "news": cmd_news_warm,
    "accuracy": cmd_accuracy,
    "forecast-all": cmd_forecast_all,
    "backtest": cmd_backtest,
    "pending": cmd_pending,
    "snapshot": cmd_snapshot,
    "run": cmd_run,
}
ARG_COMMANDS = {"approve": cmd_approve, "reject": cmd_reject,
                "forecast": cmd_forecast}


def main() -> None:
    init_db()
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "run"
    if cmd in ARG_COMMANDS:
        ARG_COMMANDS[cmd](argv[1] if len(argv) > 1 else "all")
        return
    fn = COMMANDS.get(cmd)
    if not fn:
        print(f"unknown command: {cmd}\n사용 가능: "
              f"{', '.join(list(COMMANDS) + list(ARG_COMMANDS))}")
        sys.exit(1)
    fn()


if __name__ == "__main__":
    main()
