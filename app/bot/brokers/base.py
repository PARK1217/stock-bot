"""증권사 공통 인터페이스.

모든 증권사(KIS, 토스 등)는 이 추상 클래스를 구현한다.
전략/리스크/스케줄러는 BrokerAdapter 인터페이스만 알면 되므로
증권사를 자유롭게 갈아끼우거나 멀티계좌로 확장할 수 있다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class Position:
    symbol: str          # 종목코드 (예: "005930") / 미국 티커 (예: "AAPL")
    name: str
    qty: float           # 미국 소수점 주식 대응 위해 float
    avg_price: float
    current_price: float
    currency: str = "KRW"  # KRW | USD

    @property
    def market_value(self) -> float:
        """보유 통화 기준 평가금액."""
        return self.qty * self.current_price

    def market_value_krw(self, usdkrw: float) -> float:
        """원화 환산 평가금액 (다중통화 비중 계산용)."""
        return self.market_value * (usdkrw if self.currency == "USD" else 1.0)

    @property
    def pnl_pct(self) -> float:
        if self.avg_price == 0:
            return 0.0
        return (self.current_price - self.avg_price) / self.avg_price * 100


@dataclass
class Balance:
    cash: float                       # 주문가능 현금
    total_eval: float                 # 총평가금액
    positions: list[Position] = field(default_factory=list)


@dataclass
class OrderResult:
    ok: bool
    order_id: str | None = None
    message: str = ""
    raw: dict | None = None


class BrokerAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def get_price(self, symbol: str) -> float:
        """현재가 조회."""

    def get_prices(self, symbols: list[str]) -> dict[str, float]:
        """여러 종목 현재가 일괄 조회(기본은 개별 반복, 어댑터서 배치 오버라이드)."""
        return {s: self.get_price(s) for s in symbols}

    @abstractmethod
    def get_balance(self) -> Balance:
        """잔고 + 보유 포지션 조회."""

    @abstractmethod
    def place_order(self, symbol: str, side: Side, qty: float,
                    price: float | None = None) -> OrderResult:
        """주문. price=None 이면 시장가."""

    def usdkrw(self) -> float:
        """USD→KRW 환율. 원화 전용 증권사는 1.0(미사용). 토스에서 오버라이드."""
        return 1.0
