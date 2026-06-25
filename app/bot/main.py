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
    labels = {("63751874", "01"): "소수점주식", ("63776023", "01"): "ISA중개형",
              ("63776023", "22"): "연금저축"}
    overseas = {("63751874", "01")}  # 해외계좌(국내 잔고 API 미적용)
    g_cash = g_eval = 0.0
    lines = [f"[KIS/{_mode()}] 3계좌 통합잔고"]
    for cano, prod in settings.kis_accounts:
        label = labels.get((cano, prod), f"{cano}-{prod}")
        if (cano, prod) in overseas:
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
    head = f"🧪 모의 자동매매 · 총 {r['total']:,.0f}원 · 목표 {', '.join(r['targets'])}"
    body = "\n".join(r["orders"]) if r["orders"] else "리밸런싱 변경 없음"
    notify(head + "\n" + body)
    print(head + "\n" + body)


# ---------- 제안 ----------
def cmd_propose() -> None:
    broker, strategy, risk = _build()
    fx = broker.usdkrw()
    bal = broker.get_balance()
    prices = _gather_prices(broker, strategy, bal)

    signals = risk.stop_loss_signals(bal)
    signals += strategy.generate(bal, prices, fx)

    with SessionLocal() as session:
        today = session.query(Proposal).filter(
            cast(Proposal.ts, Date) == datetime.now().date()).count()
        approved = risk.filter(signals, bal, prices, today, fx)
        if not approved:
            log.info("제안할 신호 없음 (밴드 이내 또는 차단)")
            notify("ℹ️ 리밸런싱 제안 없음 (현재 비중 목표 범위 내).")
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

    msg = ("🤖 리밸런싱 제안 ({}건)\n{}\n\n승인: `approve all` 또는 `approve <id>`"
           "\n거절: `reject all`").format(len(lines), "\n".join(lines))
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
        msg = "🔎 추세 상위 종목\n" + "\n".join(
            f"{i+1}. {r.symbol} (점수 {r.score:.0f}, 3M "
            f"{('%+.1f%%' % r.ret_3m) if r.ret_3m is not None else 'n/a'})"
            for i, r in enumerate(top))
        notify(msg)


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
    notify(f"🔮 {symbol} {horizon}일 전망: 상승확률 {fc.prob_up*100:.0f}%, "
           f"기대 {fc.exp_return:+.1f}%, +5%도달 {fc.target_touch['+5%']*100:.0f}% "
           f"(신호적중 {('%.0f%%'%(fc.backtest_winrate*100)) if fc.backtest_winrate else 'n/a'})")


def cmd_accuracy() -> None:
    """만기된 예측을 실측과 대조 → 자기예측 정확도(캘리브레이션) 산출."""
    from datetime import timedelta
    from bot.storage.models import Prediction
    broker, _, _ = _build()
    with SessionLocal() as session:
        opens = session.query(Prediction).filter(Prediction.status == "open").all()
        for p in opens:
            due = p.made_at + timedelta(days=p.horizon_days * 1.5)  # 거래일 근사
            if datetime.now() < due:
                continue
            price = broker.get_price(p.symbol)
            if price <= 0:
                continue
            p.actual_price = price
            p.actual_return = (price / p.base_price - 1) * 100
            p.dir_hit = (p.actual_return > 0) == (p.prob_up >= 0.5)
            p.band_hit = p.p10 <= price <= p.p90
            p.evaluated_at = datetime.now()
            p.status = "evaluated"
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
    broker, _, _ = _build()
    fx = broker.usdkrw()
    bal = broker.get_balance()
    total_krw = bal.cash + sum(p.market_value_krw(fx) for p in bal.positions)
    with SessionLocal() as session:
        session.add(DailySnapshot(cash=bal.cash, total_eval=total_krw))
        session.commit()
    notify(f"📊 일일 스냅샷 현금 {bal.cash:,.0f} / 총평가(원화) {total_krw:,.0f}")


def cmd_run() -> None:
    init_db()
    sched = BlockingScheduler(timezone="Asia/Seoul")
    # 반자동: 정해진 시각에 '제안'만 생성(자동 주문 X). 평일 10:00
    sched.add_job(cmd_propose, "cron", day_of_week="mon-fri", hour=10, minute=0)
    sched.add_job(cmd_screen, "cron", day_of_week="mon-fri", hour=9, minute=10)
    sched.add_job(cmd_snapshot, "cron", day_of_week="mon-fri", hour=15, minute=40)
    # 모의 자동매매(KR+US 통합). KR장 3회(09:15·12:30·15:00, 시장가 즉시체결).
    # US장 1회(23:35, 모의 미국 체결지연으로 중복주문 방지 위해 하루 1회).
    for h, m in [(9, 15), (12, 30), (15, 0), (23, 35)]:
        sched.add_job(cmd_paper, "cron", day_of_week="mon-fri", hour=h, minute=m)
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
    "accuracy": cmd_accuracy,
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
