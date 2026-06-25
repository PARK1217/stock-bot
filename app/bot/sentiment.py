"""감성 분석 저수준 어댑터 — FinBERT(점수) + Groq(추론).

역할 분담:
  - FinBERT(ProsusAI/finbert): 금융 헤드라인 감성 점수 전문. 로컬·무료·빠름.
  - Groq(LLM): 관련성 필터("이 헤드라인이 해당 종목 얘긴가") + 요약. 무료 API.

둘 다 선택적 — 없으면 상위(news.py)에서 키워드로 폴백.
"""
from __future__ import annotations

import json
import logging
import time

import httpx

from bot.config import settings

log = logging.getLogger(__name__)

_finbert_pipe = None
_finbert_failed = False


# ---------------- FinBERT (로컬 감성 점수) ----------------
def _get_finbert():
    global _finbert_pipe, _finbert_failed
    if _finbert_pipe is not None or _finbert_failed:
        return _finbert_pipe
    try:
        from transformers import pipeline
        _finbert_pipe = pipeline("text-classification", model="ProsusAI/finbert",
                                 top_k=None)
    except Exception as e:  # noqa: BLE001
        log.warning("FinBERT 로드 실패(%s) → 폴백", e)
        _finbert_failed = True
    return _finbert_pipe


def _parse_finbert(scores: list[dict]) -> tuple[str, float]:
    d = {s["label"].lower(): s["score"] for s in scores}
    signed = d.get("positive", 0.0) - d.get("negative", 0.0)
    label = max(d, key=d.get) if d else "neutral"
    return label, signed


def _finbert_hf_one(text: str) -> list[dict] | None:
    """HF Inference API FinBERT 단일 호출. 모델 로딩(503) 재시도. 라벨 dict 리스트 반환.
    (라우터 엔드포인트는 배치 리스트를 제대로 처리 못 해 텍스트당 1건씩 호출)"""
    url = f"https://router.huggingface.co/hf-inference/models/{settings.hf_finbert_model}"
    headers = {"Authorization": f"Bearer {settings.huggingface_api_key}"}
    for attempt in range(4):
        try:
            r = httpx.post(url, headers=headers,
                           json={"inputs": text, "options": {"wait_for_model": True}},
                           timeout=30)
            if r.status_code == 503:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("HF FinBERT 호출 실패: %s", e)
            return None
        # 단일: [[{label,score}x3]]
        if isinstance(data, list) and data and isinstance(data[0], list):
            return data[0]
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data
        return None
    return None


def _finbert_hf(texts: list[str]) -> list[tuple[str, float]] | None:
    results = []
    for t in texts:
        scores = _finbert_hf_one(t)
        if scores is None:
            return None  # 한 건이라도 실패하면 로컬 폴백
        results.append(_parse_finbert(scores))
    return results


def finbert_scores(texts: list[str]) -> list[tuple[str, float]] | None:
    """각 텍스트 → (label, signed_score). signed = P(pos) - P(neg) ∈ [-1,1].
    HF 키 있으면 HF Inference API(오프로딩), 없으면 로컬 transformers, 둘 다 없으면 None."""
    if not texts:
        return None
    if settings.huggingface_api_key:
        r = _finbert_hf(texts)
        if r is not None:
            return r  # HF 실패 시 로컬로 폴백
    if not settings.use_finbert:
        return None
    pipe = _get_finbert()
    if pipe is None:
        return None
    out = pipe(texts, truncation=True, max_length=128)
    return [_parse_finbert(s) for s in out]


# ---------------- Groq (관련성·요약) ----------------
def _openai_chat(base_url: str, key: str, model: str,
                 prompt: str, max_tokens: int) -> str | None:
    """OpenAI 호환 chat completions(그록·미스트랄 공용). 429/5xx 백오프."""
    for attempt in range(3):
        try:
            r = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "max_tokens": max_tokens, "temperature": 0,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=30)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(0.6 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError):
            time.sleep(0.4 * (attempt + 1))
    return None


def _llm_chat(prompt: str, max_tokens: int = 300) -> str | None:
    """관련성·요약용 LLM. Groq 우선 → 실패 시 Mistral 폴백."""
    if settings.groq_api_key:
        out = _openai_chat(settings.groq_base_url, settings.groq_api_key,
                           settings.groq_model, prompt, max_tokens)
        if out is not None:
            return out
        log.warning("Groq 실패 → Mistral 폴백")
    if settings.mistral_api_key:
        return _openai_chat(settings.mistral_base_url, settings.mistral_api_key,
                            settings.mistral_model, prompt, max_tokens)
    return None


def groq_relevant_indices(symbol: str, headlines: list[str]) -> list[int] | None:
    """해당 종목과 관련 있는 헤드라인 인덱스만 반환. 키 없으면 None(필터 안 함)."""
    if not (settings.groq_api_key or settings.mistral_api_key) or not headlines:
        return None
    numbered = "\n".join(f"{i}: {h}" for i, h in enumerate(headlines))
    prompt = (f"종목 {symbol} 와 직접 관련된 헤드라인의 번호만 JSON 배열로 답하라. "
              f"무관하면 제외. 예: [0,3,5]\n{numbered}")
    txt = _llm_chat(prompt, 120)
    if not txt:
        return None
    try:
        arr = json.loads(txt[txt.find("["):txt.rfind("]") + 1])
        return [int(i) for i in arr if isinstance(i, (int, float))]
    except (ValueError, json.JSONDecodeError):
        return None


def groq_summary(symbol: str, headlines: list[str]) -> str:
    if not (settings.groq_api_key or settings.mistral_api_key) or not headlines:
        return ""
    joined = "\n".join(f"- {h}" for h in headlines[:10])
    txt = _llm_chat(f"{symbol} 관련 최근 뉴스를 한국어 평문 한 문장으로 요약하라"
                    f"(마크다운·별표·특수기호 없이):\n{joined}", 120)
    return (txt or "").strip()[:120]
