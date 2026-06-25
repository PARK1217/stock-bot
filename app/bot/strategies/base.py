"""전략 인터페이스.

전략은 '현재 잔고 + 시세 + 환율'을 입력받아 '목표 주문 목록(Signal)'을 반환한다.
실제 주문 실행/리스크 검증은 호출자(main)가 담당 → 전략은 순수 로직.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from bot.brokers.base import Balance, Side


@dataclass
class Signal:
    symbol: str
    side: Side
    qty: float
    currency: str = "KRW"   # 종목 거래통화 (KR=KRW, US=USD)
    reason: str = ""


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def generate(self, balance: Balance, prices: dict[str, float],
                 usdkrw: float = 1.0) -> list[Signal]:
        ...
