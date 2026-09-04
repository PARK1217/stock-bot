"""통합 알림 — 디스코드 우선 → 텔레그램 폴백 → 콘솔 로그.
모든 알림은 대시보드 🔔 알림센터용 redis 로그(notif:log)에도 적재(실패해도 발송엔 무영향)."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def notify(text: str) -> None:
    try:                                   # 대시보드 알림센터(종모양 팝업) 로그
        import json
        import time
        import redis
        from bot.config import settings
        r = redis.from_url(settings.redis_url)
        r.lpush("notif:log", json.dumps({"ts": int(time.time()), "text": (text or "")[:1500]},
                                        ensure_ascii=False))
        r.ltrim("notif:log", 0, 99)
    except Exception:  # noqa: BLE001
        pass
    from bot.notify import discord, telegram
    if discord.send(text) or telegram.send(text):
        return
    log.info("[notify] %s", text)
