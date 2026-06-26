"""리스크 게이트 — 전략 신호를 주문/제안 전에 검증·차단.

반자동(제안→승인) 모드에서는 사람이 최종 게이트이므로 여기 검증은 '보조'.
원화 환산 기준으로 종목 비중·일일 건수를 점검한다.
"""
from __future__ import annotations

import logging

from bot.brokers.base import Balance, Side
from bot.config import settings
from bot.strategies.base import Signal

log = logging.getLogger(__name__)


class RiskManager:
    def __init__(self):
        self.max_position_pct = settings.max_position_pct
        self.daily_order_limit = settings.daily_order_limit
        self.stop_loss_pct = settings.stop_loss_pct

    def filter(self, signals: list[Signal], balance: Balance,
               prices: dict[str, float], orders_today: int,
               usdkrw: float = 1.0) -> list[Signal]:
        approved: list[Signal] = []
        total_krw = balance.cash + sum(
            p.market_value_krw(usdkrw) for p in balance.positions) or 1.0
        held = {p.symbol: p for p in balance.positions}
        budget = orders_today
        cash_left = balance.cash            # 원화 가용현금 — 매수마다 차감(매수여력 게이트)

        for s in signals:
            if budget >= self.daily_order_limit:
                log.warning("일일 주문 한도 도달 → %s 이후 차단", s.symbol)
                break
            price = prices.get(s.symbol, 0)
            if price <= 0:
                continue
            fx = usdkrw if s.currency == "USD" else 1.0

            if s.side == Side.BUY:
                cost_krw = s.qty * price * fx
                if cost_krw > cash_left:    # 매수여력 초과 → 차단(현금 없는데 매수제안 방지)
                    log.warning("매수여력 부족 → %s 차단 (필요 %.0f > 가용 %.0f)",
                                s.symbol, cost_krw, cash_left)
                    continue
                cur_krw = (held[s.symbol].market_value_krw(usdkrw)
                           if s.symbol in held else 0.0)
                if (cur_krw + cost_krw) / total_krw * 100 > self.max_position_pct:
                    log.warning("종목 비중 한도 초과 → %s 차단", s.symbol)
                    continue
                cash_left -= cost_krw        # 승인된 매수만큼 가용현금 차감

            approved.append(s)
            budget += 1
        return approved

    def stop_loss_signals(self, balance: Balance,
                          candles_by_symbol: dict | None = None) -> list[Signal]:
        """손절선을 넘긴 보유 종목 전량 매도 신호.
        candles 주어지면 **SMA50 아래(실제 하락추세)일 때만** 발동 — 인컴/커버드콜
        ETF가 분배(배당) 침식으로 가격만 빠진 경우(추세는 유지) 오발동 방지."""
        from bot.screener import sma
        out: list[Signal] = []
        for p in balance.positions:
            if p.pnl_pct > -self.stop_loss_pct:
                continue
            if candles_by_symbol and p.symbol in candles_by_symbol:
                closes = [c["close"] for c in candles_by_symbol[p.symbol]
                          if c.get("close", 0) > 0]
                s50 = sma(closes, 50)
                if s50 and closes and closes[-1] >= s50:    # 추세 유지 → 손절 보류
                    log.info("손절 보류(추세유지·분배침식 추정): %s pnl=%.1f%% ≥SMA50",
                             p.symbol, p.pnl_pct)
                    continue
            out.append(Signal(p.symbol, Side.SELL, p.qty,
                              currency=p.currency,
                              reason=f"stop_loss {p.pnl_pct:.1f}%"))
        return out
