from bot.brokers.base import BrokerAdapter, Position, Balance, OrderResult
from bot.brokers.kis import KISBroker
from bot.brokers.toss import TossBroker
from bot.config import settings

__all__ = ["BrokerAdapter", "Position", "Balance", "OrderResult",
           "KISBroker", "TossBroker", "get_broker"]

_REGISTRY = {"kis": KISBroker, "toss": TossBroker}


def get_broker(name: str | None = None) -> BrokerAdapter:
    """설정(BROKER) 또는 인자로 증권사 어댑터 선택."""
    key = (name or settings.broker).lower()
    if key not in _REGISTRY:
        raise ValueError(f"unknown broker: {key} (가능: {', '.join(_REGISTRY)})")
    return _REGISTRY[key]()
