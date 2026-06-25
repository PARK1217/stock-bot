import json
import sys
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from bot.config import settings  # noqa: E402

url = "https://router.huggingface.co/hf-inference/models/ProsusAI/finbert"
h = {"Authorization": f"Bearer {settings.huggingface_api_key}"}
batch = ["Nvidia stock gains on strong demand", "JEPQ: No Reason To Hold It"]
r = httpx.post(url, headers=h, json={"inputs": batch}, timeout=30)
print("BATCH status", r.status_code)
print(json.dumps(r.json())[:700])
# 단일 입력도 확인
r2 = httpx.post(url, headers=h, json={"inputs": batch[0]}, timeout=30)
print("\nSINGLE status", r2.status_code)
print(json.dumps(r2.json())[:400])
