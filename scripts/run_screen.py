"""실제 bot.screener 코드로 라이브 추세 스크리닝 (읽기 전용, 주문 없음).
Redis/Postgres 불필요 — 캔들만 httpx로 받아 점수화."""
from __future__ import annotations

import base64
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from bot.screener import screen, score_symbol, DEFAULT_WATCHLIST  # noqa: E402

HOST = "https://openapi.tossinvest.com"


def load_env(p):
    env = {}
    for line in Path(p).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.split(" #")[0].strip()
    return env


env = load_env(ROOT / ".env")
c = httpx.Client(base_url=HOST, timeout=20.0)
basic = base64.b64encode(
    f"{env['TOSS_APP_KEY']}:{env['TOSS_APP_SECRET']}".encode()).decode()
tok = c.post("/oauth2/token", headers={"Authorization": f"Basic {basic}",
             "Content-Type": "application/x-www-form-urlencoded"},
             data={"grant_type": "client_credentials"}).json()["access_token"]
auth = {"Authorization": f"Bearer {tok}"}


def candles(sym):
    for attempt in range(4):
        r = c.get("/api/v1/candles", headers=auth,
                  params={"symbol": sym, "interval": "1d", "count": 200, "adjusted": "true"})
        if r.status_code == 200:
            break
        if r.status_code == 429:
            time.sleep(0.5 * (attempt + 1))
            continue
        return []
    else:
        return []
    rows = r.json().get("result", {}).get("candles", [])
    out = [{"ts": x["timestamp"], "close": float(x["closePrice"] or 0)} for x in rows]
    out.sort(key=lambda z: z["ts"])
    return out


data = {}
for s in DEFAULT_WATCHLIST:
    cs = candles(s)
    if cs:
        data[s] = cs
    print(f"  {s}: {len(cs)}봉", file=sys.stderr)

results = screen(data)
print(f"\n추세 스크리닝 결과 ({len(results)}종목, 점수순)\n" + "=" * 78)
for r in results:
    print(r.line())
