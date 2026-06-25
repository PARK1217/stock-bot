"""통합 알림 — 디스코드 우선 → 텔레그램 폴백 → 콘솔 로그."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def notify(text: str) -> None:
    from bot.notify import discord, telegram
    if discord.send(text) or telegram.send(text):
        return
    log.info("[notify] %s", text)
