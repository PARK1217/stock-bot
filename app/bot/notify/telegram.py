"""텔레그램 알림 (선택). 토큰 미설정 시 콘솔 로그로 대체."""
from __future__ import annotations

import logging

import httpx

from bot.config import settings

log = logging.getLogger(__name__)


def notify(text: str) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        log.info("[notify] %s", text)
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": text},
            timeout=10,
        )
    except httpx.HTTPError:
        log.exception("텔레그램 전송 실패")
