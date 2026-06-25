"""디스코드 웹훅 알림 — 웹훅 URL 하나면 끝(봇 토큰 불필요)."""
from __future__ import annotations

import logging

import httpx

from bot.config import settings

log = logging.getLogger(__name__)


def send(text: str) -> bool:
    if not settings.discord_webhook_url:
        return False
    try:
        r = httpx.post(settings.discord_webhook_url,
                       json={"content": text[:1900]}, timeout=10)  # 디스코드 2000자 제한
        r.raise_for_status()
        return True
    except httpx.HTTPError:
        log.warning("디스코드 전송 실패")
        return False
