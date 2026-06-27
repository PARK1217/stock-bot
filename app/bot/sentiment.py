"""감성 분석 저수준 어댑터 — FinBERT(점수) + Groq(추론).

역할 분담:
  - FinBERT(ProsusAI/finbert): 금융 헤드라인 감성 점수 전문. 로컬·무료·빠름.
  - Groq(LLM): 관련성 필터("이 헤드라인이 해당 종목 얘긴가") + 요약. 무료 API.

둘 다 선택적 — 없으면 상위(news.py)에서 키워드로 폴백.
"""
from __future__ import annotations

import json
import logging
import re
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
    """HF Inference API FinBERT — inputs에 리스트를 한 번에(배치). 50건도 ~0.8초로,
    텍스트당 1콜(과거 ~13초) 대비 약 17배. 배치 형식이 깨지면 순차 폴백."""
    url = f"https://router.huggingface.co/hf-inference/models/{settings.hf_finbert_model}"
    headers = {"Authorization": f"Bearer {settings.huggingface_api_key}"}
    for attempt in range(4):
        try:
            r = httpx.post(url, headers=headers,
                           json={"inputs": texts, "options": {"wait_for_model": True}},
                           timeout=60)
            if r.status_code == 503:                      # 모델 콜드로딩
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            break
        except (httpx.HTTPError, ValueError) as e:
            log.warning("HF FinBERT 배치 실패: %s", e)
            data = None
            break
    else:
        data = None
    # 배치 정상: [[{label,score}x3], ...] (텍스트당 라벨 리스트)
    if (isinstance(data, list) and len(data) == len(texts)
            and data and isinstance(data[0], list)):
        return [_parse_finbert(d) for d in data]
    # 배치 형식 예상밖/실패 → 한 건씩 순차(기존 방식) 폴백
    results = []
    for t in texts:
        scores = _finbert_hf_one(t)
        if scores is None:
            return None
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
                 prompt: str, max_tokens: int, *, retry_rl: bool = True) -> str | None:
    """OpenAI 호환 chat completions(그록·미스트랄 공용). 429/5xx 백오프.
    retry_rl=False면 429(레이트리밋)는 재시도 없이 즉시 None — 폴백이 있는 1차 LLM용
    (특히 Groq 일일 토큰한도(TPD)는 수초 재시도로 안 풀려 지연만 키움)."""
    for attempt in range(3):
        try:
            r = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "max_tokens": max_tokens, "temperature": 0,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=30)
            if r.status_code == 429 and not retry_rl:
                return None                                # 폴백으로 바로 넘김
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(0.6 * (attempt + 1))
                continue
            if r.status_code in (401, 403, 404, 400):     # 영구 오류 → 재시도 무의미
                log.warning("LLM %s 영구오류 %s, 폴백", model, r.status_code)
                return None
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError):
            time.sleep(0.4 * (attempt + 1))
    return None


def _llm_chat(prompt: str, max_tokens: int = 300, model: str | None = None) -> str | None:
    """관련성·요약용 LLM. Groq 우선 → 실패 시 Mistral 폴백.
    model 미지정 시 관련성용 경량모델(groq_news_model). 요약은 더 강한 모델 전달."""
    if settings.groq_api_key:
        out = _openai_chat(settings.groq_base_url, settings.groq_api_key,
                           model or settings.groq_news_model, prompt, max_tokens, retry_rl=False)
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


# 한자 누출 대비 후처리(드물게 LLM이 섞어 내보냄). 금융뉴스 맥락서 안전한 것만.
_HANJA = {"美": "미국", "中": "중국", "日": "일본", "韓": "한국", "獨": "독일", "英": "영국",
          "佛": "프랑스", "株": "주식", "增": "증가", "減": "감소", "黑字": "흑자", "赤字": "적자"}


def _to_hangul(s: str) -> str:
    for h, k in _HANJA.items():
        s = s.replace(h, k)
    return s


def _trim_sentence(s: str, n: int = 220) -> str:
    """길면 마지막 완결 문장까지만(중간에 끊기는 것 방지)."""
    if len(s) <= n:
        return s
    cut = s[:n]
    i = max(cut.rfind(". "), cut.rfind("다. "), cut.rfind("다 "), cut.rfind("."))
    return (cut[:i + 1] if i > 60 else cut).rstrip()


def groq_summary(symbol: str, headlines: list[str]) -> str:
    """핵심 '신호 키워드'를 가운뎃점(·)으로 구분해 한 줄로 추출. 긴 요약문이 아니라
    호재/악재를 한눈에 보여주는 단어 위주(예: '2분기 실적 부진 · 주가 5% 급락').
    주목할 신호 없으면 빈 문자열(화면서 숨김)."""
    if not (settings.groq_api_key or settings.mistral_api_key) or not headlines:
        return ""
    joined = "\n".join(f"- {h}" for h in headlines[:10])
    txt = _llm_chat(
        f"{symbol} 관련 영어 뉴스 헤드라인이다. 이 종목의 핵심 '신호 키워드'를 한국어로 2~4개 뽑아 "
        f"가운뎃점(·)으로 구분해 한 줄로만 답하라.\n"
        f"규칙: ①긴 설명·문장 금지 — 짧은 키워드/구만(예: '2분기 실적 부진 · 주가 5% 급락', "
        f"'월배당 인상 · 신고가'). ②호재면 호재 단어(상승·최고치·수요증가·인상 등), 악재면 악재 단어"
        f"(급락·부진·하향·매도세 등)를 분명히 담아라. ③순한글만(한자·중국어·일본어 금지). "
        f"④마크다운·따옴표 없이. ⑤단순 시세·홍보뿐이거나 주목할 신호 없으면 정확히 'NONE'만.\n{joined}",
        120, model=settings.groq_summary_model)
    out = (txt or "").strip().strip('"').strip("'")
    if (not out or out.upper().startswith("NONE")
            or "특이사항 없" in out or "별다른 이슈" in out or "특별한 이슈" in out):
        return ""                                     # 신호 없음 → 빈값(프론트서 숨김)
    out = _to_hangul(out)
    out = re.sub(r"[_*`#]", " ", out)                 # 밑줄·마크다운 잔여 제거
    out = re.sub(r"[㐀-鿿]", "", out)         # 남은 한자(CJK) 제거(고유명사 깨짐 방지)
    out = re.sub(r"[•\-▪]\s*", "· ", out)             # 불릿류 → 가운뎃점 통일
    out = re.sub(r"\s{2,}", " ", out).strip(" ·\n")
    return out[:140]
