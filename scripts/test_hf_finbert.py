"""HF Inference API FinBERT 검증 — 로컬 결과와 비교."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from bot.config import settings        # noqa: E402
from bot.sentiment import _finbert_hf  # noqa: E402

print("HF key present:", bool(settings.huggingface_api_key), settings.hf_finbert_model)
samples = [
    "Nvidia Stock Gains Even as AI Chip Competition Hots Up",
    "JEPQ: No Reason To Hold It",
    "Realty Income raises monthly dividend for 135th time",
    "Tesla under fresh scrutiny as crash victim's family files wrongful-death suit",
]
res = _finbert_hf(samples)
if res is None:
    print("HF 호출 실패(None) — 응답/키 확인 필요")
else:
    for txt, (label, signed) in zip(samples, res):
        print(f"  {signed:+.2f} [{label:<8}] {txt[:60]}")
