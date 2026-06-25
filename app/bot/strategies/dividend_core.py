"""배당 안정형 코어 전략 — 미국 + 한국 혼합 바스켓.

방식:
  - 목표 바스켓을 (종목, 시장, 목표비중)으로 정의. 비중은 '원화 환산 기준' 합계 1.0.
  - 현재 비중이 목표 대비 밴드(±rebalance_band)를 벗어난 종목만 리밸런싱 신호 생성.
  - 미국 종목은 USD, 한국 종목은 KRW로 주문. 비중 계산은 환율로 원화 통일.
  - 미국은 소수점 주문 가능(qty float), 한국은 정수주.

주의: 이 바스켓은 '예시'다. 실제 종목/비중은 본인 판단으로 교체할 것.
사용자 토스 계좌엔 이미 미국 배당주(O 등)가 있으니 그에 맞춰 조정 권장.
"""
from __future__ import annotations

from dataclasses import dataclass

from bot.brokers.base import Balance, Side
from bot.strategies.base import Signal, Strategy


@dataclass
class BasketItem:
    symbol: str
    market: str        # "KR" | "US"
    weight: float      # 목표비중 (원화환산 기준, 전체 합 1.0)
    name: str = ""

    @property
    def currency(self) -> str:
        return "USD" if self.market == "US" else "KRW"


# 예시 혼합 바스켓 — 미국 배당 + 한국 배당/ETF. 합계 1.0.
DEFAULT_BASKET: list[BasketItem] = [
    BasketItem("O", "US", 0.20, "Realty Income (월배당 리츠)"),
    BasketItem("SCHD", "US", 0.25, "美 배당성장 ETF"),
    BasketItem("JEPI", "US", 0.15, "美 커버드콜 인컴 ETF"),
    BasketItem("161510", "KR", 0.20, "PLUS 고배당주"),
    BasketItem("069500", "KR", 0.20, "KODEX 200"),
]


class DividendCoreStrategy(Strategy):
    name = "dividend_core"

    def __init__(self, basket: list[BasketItem] | None = None,
                 rebalance_band: float = 0.05):
        self.basket = basket or DEFAULT_BASKET
        self.band = rebalance_band

    def generate(self, balance: Balance, prices: dict[str, float],
                 usdkrw: float = 1.0) -> list[Signal]:
        # 총자산(원화환산) = 현금 + 보유 평가
        total = balance.cash + sum(
            p.market_value_krw(usdkrw) for p in balance.positions
        )
        if total <= 0:
            return []

        held = {p.symbol: p for p in balance.positions}
        signals: list[Signal] = []

        for item in self.basket:
            price = prices.get(item.symbol)
            if not price:
                continue
            fx = usdkrw if item.market == "US" else 1.0

            cur_krw = (held[item.symbol].market_value_krw(usdkrw)
                       if item.symbol in held else 0.0)
            cur_w = cur_krw / total
            if abs(cur_w - item.weight) < self.band:
                continue  # 밴드 안 → 유지

            target_krw = item.weight * total
            diff_krw = target_krw - cur_krw
            diff_native = diff_krw / fx           # 주문 통화 기준 금액
            raw_qty = abs(diff_native) / price

            # 한국=정수주, 미국=소수점(6자리)
            qty = int(raw_qty) if item.market == "KR" else round(raw_qty, 6)
            if qty <= 0:
                continue

            signals.append(Signal(
                symbol=item.symbol,
                side=Side.BUY if diff_krw > 0 else Side.SELL,
                qty=qty,
                currency=item.currency,
                reason=f"{item.market} rebalance {cur_w:.1%}->{item.weight:.0%}",
            ))
        return signals
