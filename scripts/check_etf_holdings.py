"""Finnhub ETF 구성종목 API가 무료티어에서 되는지 확인."""
import sys
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from bot.config import settings  # noqa: E402

key = settings.finnhub_api_key
for ep, params in [
    ("etf/holdings", {"symbol": "JEPQ"}),
    ("etf/profile", {"symbol": "JEPQ"}),
    ("etf/holdings", {"symbol": "SCHD"}),
]:
    p = {**params, "token": key}
    r = httpx.get(f"https://finnhub.io/api/v1/{ep}", params=p, timeout=20)
    print(f"\n=== GET /{ep}?symbol={params['symbol']}  HTTP {r.status_code} ===")
    txt = r.text
    print(txt[:600])
