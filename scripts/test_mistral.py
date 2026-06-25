"""Mistral 폴백 검증 — 직접 호출 + _llm_chat 통합."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from bot.config import settings                       # noqa: E402
from bot.sentiment import _openai_chat, _llm_chat     # noqa: E402

print("Mistral key:", bool(settings.mistral_api_key), settings.mistral_model)
print("Groq key:", bool(settings.groq_api_key), settings.groq_model)

print("\n[1] Mistral 직접 호출 (폴백 경로):")
out = _openai_chat(settings.mistral_base_url, settings.mistral_api_key,
                   settings.mistral_model, "한국어로 'OK'만 답해", 10)
print("  →", repr(out))

print("\n[2] _llm_chat (Groq 우선):")
out2 = _llm_chat("한국어로 'OK'만 답해", 10)
print("  →", repr(out2))

print("\n[3] Groq 강제실패 시 Mistral 폴백 시뮬:")
g = settings.groq_api_key
settings.groq_api_key = "bad-key-force-fail"  # Groq 실패 유도
out3 = _llm_chat("한국어로 'OK'만 답해", 10)
settings.groq_api_key = g
print("  →", repr(out3), "(Mistral이 받았으면 성공)")
