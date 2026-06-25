"""실제 토스 보유로 교체 제안 데모 (읽기전용, 주문 없음).
계좌별 유니버스+처짐규칙으로 '갈아타기' 후보를 제안."""
from __future__ import annotations

import base64
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from bot.accounts import PROFILES                 # noqa: E402
from bot.reallocation import propose_switches     # noqa: E402

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
seq = str(c.get("/api/v1/accounts", headers=auth).json()["result"][0]["accountSeq"])
acc = {**auth, "X-Tossinvest-Account": seq}

held = [h["symbol"] for h in
        c.get("/api/v1/holdings", headers=acc).json()["result"]["items"]]
print("보유:", ", ".join(held))

_cache: dict[str, list[float]] = {}


def get_closes(sym):
    if sym in _cache:
        return _cache[sym]
    rows = []
    for attempt in range(4):
        r = c.get("/api/v1/candles", headers=auth,
                  params={"symbol": sym, "interval": "1d", "count": 200, "adjusted": "true"})
        if r.status_code == 200:
            rows = r.json()["result"]["candles"]
            break
        if r.status_code == 429:
            time.sleep(0.5 * (attempt + 1))
    closes = [float(x["closePrice"]) for x in sorted(rows, key=lambda z: z["timestamp"])
              if float(x["closePrice"]) > 0]
    _cache[sym] = closes
    return closes


profile = PROFILES["toss"]
print(f"\n프로필: {profile.label}  (처짐<{profile.drop_score:g}, "
      f"교체마진 {profile.switch_margin:g}, 최대 {profile.max_switches}건)\n")

proposals = propose_switches(profile, held, get_closes)
if not proposals:
    print("교체 제안 없음 — 처진 보유가 없거나 확실히 더 나은 후보가 없음(보수적 통과).")
else:
    for p in proposals:
        print("  " + p.line())
