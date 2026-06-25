"""텔레그램 알림 (폴백). 토큰 미설정 시 False 반환."""
from __future__ import annotations

import logging

import httpx

from bot.config import settings

log = logging.getLogger(__name__)


def send(text: str) -> bool:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": text}, timeout=10)
        r.raise_for_status()
        return True
    except httpx.HTTPError:
        log.warning("텔레그램 전송 실패")
        return False
