"""백테스트용 장기 일봉 데이터 — Tiingo EOD.

브로커(토스 200봉 하드캡)와 별개로 다년치 US 일봉을 가져와 다(多)레짐 백테스트
(2020 폭락·2022 하락장 포함)에 사용. adjusted=True면 분배·분할 조정가(adjClose)라
**배당 재투자 반영 총수익 기준** → 인컴/배당 ETF 비교가 정확해진다.
"""
from __future__ import annotations

import json
import logging

import httpx
import redis

from bot.config import settings

log = logging.getLogger(__name__)
_BASE = "https://api.tiingo.com/tiingo/daily"
_redis = redis.from_url(settings.redis_url)


def tiingo_candles(symbol: str, start: str = "2018-01-01",
                   adjusted: bool = True) -> list[dict]:
    """Tiingo 일봉 → [{ts, open, high, low, close, volume}] 과거→최근.
    키 없거나 실패 시 빈 리스트(호출측 폴백). adjusted=총수익(분배·분할 조정).
    24h redis 캐시(일봉은 하루1회 변경 → 무료티어 레이트리밋 429 회피)."""
    key = settings.tiingo_api_key
    if not key:
        return []
    ck = f"hist:tiingo:{symbol}:{start}:{int(adjusted)}"
    cached = _redis.get(ck)
    if cached:
        try:
            return json.loads(cached)
        except (TypeError, ValueError):
            pass
    try:
        r = httpx.get(f"{_BASE}/{symbol}/prices",
                      params={"startDate": start, "token": key, "format": "json"},
                      headers={"Content-Type": "application/json"}, timeout=20.0)
        r.raise_for_status()
        rows = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("tiingo %s 실패: %s", symbol, e)
        return []
    pre = "adj" if adjusted else ""

    def f(row: dict, field: str) -> float:
        key_ = f"{pre}{field.capitalize()}" if pre else field
        return float(row.get(key_) or 0)

    out = [{"ts": (row.get("date") or "")[:10],
            "open": f(row, "open"), "high": f(row, "high"),
            "low": f(row, "low"), "close": f(row, "close"),
            "volume": float(row.get("adjVolume" if adjusted else "volume") or 0)}
           for row in rows if row.get("date")]
    out.sort(key=lambda c: c["ts"])
    if out:                                   # 성공시만 캐시(429/빈응답은 캐시 안 함)
        try:
            _redis.set(ck, json.dumps(out), ex=86400)
        except Exception:  # noqa: BLE001
            pass
    return out
