"""종목명 자동 학습·영구 저장.

증권사(토스/한투/KIS)가 보유·잔고 응답에 실어주는 종목명을 Redis 해시에 누적한다.
→ 한 번 사서 보유한 종목은 이름이 영구 기억되어, 나중에 팔아도 거래내역·노출 등에서
   계속 종목명이 표시된다(수동으로 코드에 이름을 추가할 필요 없음).

한글 라벨은 프론트의 KNM(시드 맵)이 우선이고, 여기서 학습한 증권사 이름(US는 영어)은
KNM에 없는 종목의 자동 폴백으로 쓰인다.
"""
from __future__ import annotations

import redis

from bot.config import settings

_r = redis.from_url(settings.redis_url)
_KEY = "names:learned"


def learn(symbol: str, name: str) -> None:
    """심볼→이름 학습(영구 저장). 빈 값·티커와 동일한 이름은 무시."""
    if not symbol or not name:
        return
    name = str(name).strip()
    if not name or name == symbol:
        return
    try:
        _r.hset(_KEY, symbol, name)
    except Exception:  # noqa: BLE001  (이름 학습 실패는 치명적 아님)
        pass


def learn_positions(positions) -> None:
    """[{symbol, name}, ...]에서 일괄 학습."""
    for p in positions or []:
        try:
            learn(p.get("symbol", ""), p.get("name") or "")
        except Exception:  # noqa: BLE001
            pass


def all_learned() -> dict:
    """학습된 전체 심볼→이름 맵."""
    try:
        return {k.decode(): v.decode() for k, v in _r.hgetall(_KEY).items()}
    except Exception:  # noqa: BLE001
        return {}
