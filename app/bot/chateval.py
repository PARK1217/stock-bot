"""챗봇 콜(매수·매도 / 상승·하락) 자동 기록·채점.

챗봇 답변에서 '종목 + 방향' 콜을 휴리스틱으로 추출 → 당시 가격과 함께 Redis에 기록 →
21일 뒤 실제 가격으로 맞았는지 채점(예측 검증과 동일 방식). RAG평가 페이지 '챗봇 정확도'.
※자유 텍스트 추출이라 일부 누락·오인식 가능(참고용).
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta

import httpx
import redis

from bot.config import settings

log = logging.getLogger(__name__)
_r = redis.from_url(settings.redis_url)
_CALLS = "chat:calls"        # 콜 기록(JSON 리스트, 최신 앞)
_LOG = "chat:log"            # Q&A 기록(JSON 리스트, 최신 앞)
HORIZON = 21

_UP = ["매수", "사라", "사면", "사두", "담기", "담아", "추천", "비중확대", "비중 확대",
       "상승", "오를", "오른", "오름", "유망", "긍정", "buy", "long", "들어갈", "매집", "저점매수"]
_DOWN = ["매도", "팔", "정리", "손절", "비중축소", "비중 축소", "하락", "내릴", "내림",
         "빼", "줄여", "부정", "sell", "익절", "차익실현", "고점"]


def _price(symbol: str) -> float:
    try:
        if symbol[:1].isdigit():
            from bot.brokers.kis import KISBroker
            return KISBroker(account=("63776023", "01"), paper=False).get_price(symbol)
        from bot.brokers.toss import TossBroker
        return TossBroker().get_price(symbol)
    except Exception:  # noqa: BLE001
        return 0.0


def extract_calls(reply: str, known: list[str]) -> list[dict]:
    """답변에서 (종목, 방향) 콜 추출 — 종목 토큰에 '가장 가까운' 방향 키워드로 판정.
    (윈도우만 보면 옆 문장 키워드까지 잡혀 up/down 충돌 → 최근접 키워드 채택)."""
    if not reply:
        return []
    out, seen = [], set()
    for sym in known:
        # 단어 경계 매칭(부분문자열 오인식 방지: 'V'가 'NVDA' 안에 잡히는 문제)
        m = re.search(r"(?<![A-Za-z0-9])" + re.escape(sym) + r"(?![A-Za-z0-9])", reply)
        if not m or sym in seen:
            continue
        idx = m.start()
        ws, we = max(0, idx - 30), idx + 30          # 종목 주변
        win = reply[ws:we]

        def nearest(keys):                            # 종목에서 가장 가까운 키워드 거리
            best = 9999
            for k in keys:
                p = win.find(k)
                if p >= 0:
                    best = min(best, abs((ws + p) - idx))
            return best

        du, dd = nearest(_UP), nearest(_DOWN)
        if du == dd:                                  # 둘 다 없음 or 동일거리 → 불명확, 스킵
            continue
        seen.add(sym)
        out.append({"symbol": sym, "direction": "up" if du < dd else "down"})
    return out


def log_interaction(question: str, reply: str, who: str, known: list[str]) -> int:
    """Q&A 저장 + 콜 추출·기록. 반환=기록된 콜 수."""
    ts = datetime.now().isoformat()
    try:
        _r.lpush(_LOG, json.dumps({"ts": ts, "q": question[:300], "a": reply[:1500], "who": who}))
        _r.ltrim(_LOG, 0, 199)
    except Exception:  # noqa: BLE001
        pass
    n = 0
    for c in extract_calls(reply, known):
        bp = _price(c["symbol"])
        if bp <= 0:
            continue
        rec = {"ts": ts, "symbol": c["symbol"], "direction": c["direction"],
               "base_price": round(bp, 4), "horizon": HORIZON, "status": "open",
               "q": question[:120]}
        try:
            _r.lpush(_CALLS, json.dumps(rec))
            n += 1
        except Exception:  # noqa: BLE001
            pass
    try:
        _r.ltrim(_CALLS, 0, 499)
    except Exception:  # noqa: BLE001
        pass
    return n


def llm_call(prompt: str, max_tokens: int = 900) -> dict:
    """챗봇 LLM 호출(Groq→Mistral) — 토큰 usage·모델까지 반환.
    반환 {text, usage:{prompt,completion,total}, model, provider}."""
    provs = []
    if settings.groq_api_key:
        provs.append(("groq", settings.groq_base_url, settings.groq_api_key, settings.groq_model))
    if settings.mistral_api_key:
        provs.append(("mistral", settings.mistral_base_url, settings.mistral_api_key, settings.mistral_model))
    for name, base, key, model in provs:
        for attempt in range(3):
            try:
                r = httpx.post(f"{base}/chat/completions",
                               headers={"Authorization": f"Bearer {key}"},
                               json={"model": model, "max_tokens": max_tokens, "temperature": 0,
                                     "messages": [{"role": "user", "content": prompt}]},
                               timeout=30)
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(0.6 * (attempt + 1)); continue
                if r.status_code in (401, 403, 404, 400):
                    break
                r.raise_for_status()
                j = r.json()
                u = j.get("usage") or {}
                return {"text": j["choices"][0]["message"]["content"],
                        "usage": {"prompt": u.get("prompt_tokens"), "completion": u.get("completion_tokens"),
                                  "total": u.get("total_tokens")},
                        "model": model, "provider": name}
            except (httpx.HTTPError, KeyError, IndexError):
                time.sleep(0.4 * (attempt + 1))
    return {"text": None, "usage": {}, "model": None, "provider": None}


def log_chat(question: str, system_prompt: str, context: str, reply: str,
             usage: dict, model: str, provider: str, who: str, known: list[str]) -> int:
    """챗봇 1회 상호작용 풀로깅(시스템프롬프트·사용자질문·소스·답변·토큰·모델) + 콜 추출."""
    ts = datetime.now().isoformat()
    rec = {"ts": ts, "who": who, "model": model, "provider": provider,
           "user_msg": (question or "")[:1200], "system_prompt": (system_prompt or "")[:4500],
           "sources": (context or "")[:7000], "reply": (reply or "")[:3500], "usage": usage or {}}
    try:
        _r.lpush(_LOG, json.dumps(rec, ensure_ascii=False))
        _r.ltrim(_LOG, 0, 99)
    except Exception:  # noqa: BLE001
        pass
    n = 0
    for c in extract_calls(reply or "", known):
        bp = _price(c["symbol"])
        if bp <= 0:
            continue
        try:
            _r.lpush(_CALLS, json.dumps({"ts": ts, "symbol": c["symbol"], "direction": c["direction"],
                                         "base_price": round(bp, 4), "horizon": HORIZON,
                                         "status": "open", "q": (question or "")[:120]}))
            n += 1
        except Exception:  # noqa: BLE001
            pass
    try:
        _r.ltrim(_CALLS, 0, 499)
    except Exception:  # noqa: BLE001
        pass
    return n


def _all(key) -> list[dict]:
    try:
        return [json.loads(x) for x in _r.lrange(key, 0, -1)]
    except Exception:  # noqa: BLE001
        return []


def score_due() -> None:
    """만기(horizon×1.5) 지난 콜을 실제 가격으로 채점."""
    calls = _all(_CALLS)
    changed = False
    for c in calls:
        if c.get("status") != "open":
            continue
        try:
            made = datetime.fromisoformat(c["ts"])
        except Exception:  # noqa: BLE001
            continue
        if datetime.now() < made + timedelta(days=c.get("horizon", HORIZON) * 1.5):
            continue
        price = _price(c["symbol"])
        if price <= 0:
            continue
        ret = (price / c["base_price"] - 1) * 100
        c["actual_return"] = round(ret, 2)
        c["hit"] = (ret > 0) == (c["direction"] == "up")
        c["status"] = "evaluated"
        changed = True
    if changed:                              # 리스트 통째로 재기록(최신앞 순서 유지)
        try:
            _r.delete(_CALLS)
            if calls:
                _r.rpush(_CALLS, *[json.dumps(c) for c in calls])
        except Exception:  # noqa: BLE001
            pass


def report() -> dict:
    score_due()
    calls = _all(_CALLS)
    ev = [c for c in calls if c.get("status") == "evaluated"]
    hit = sum(1 for c in ev if c.get("hit"))
    acc = {"n": len(ev), "hit": hit, "acc": (hit / len(ev) if ev else 0),
           "open": sum(1 for c in calls if c.get("status") == "open")}
    return {"accuracy": acc, "calls": calls, "log": _all(_LOG)}
