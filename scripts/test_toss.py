"""토스 Open API 검증 (공식 스펙 기준, 읽기 전용/주문 없음).

순서: 토큰 -> 계좌목록 -> 현재가 -> (accountSeq로) 잔고 -> 매수가능금액.
X-Tossinvest-Account 헤더에 accountSeq vs accountNo 중 무엇이 맞는지도 자동 판별.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx

HOST = "https://openapi.tossinvest.com"


def load_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.split(" #")[0].strip()
    return env


def show(title, resp):
    print(f"\n===== {title} =====")
    print(f"HTTP {resp.status_code}  {resp.request.method} {resp.request.url}")
    try:
        print(json.dumps(resp.json(), ensure_ascii=False, indent=2)[:1500])
    except Exception:
        print(resp.text[:800])


env = load_env(Path(__file__).resolve().parent.parent / ".env")
client = httpx.Client(base_url=HOST, timeout=15.0)

basic = base64.b64encode(
    f"{env['TOSS_APP_KEY']}:{env['TOSS_APP_SECRET']}".encode()).decode()
tok = client.post("/oauth2/token",
                  headers={"Authorization": f"Basic {basic}",
                           "Content-Type": "application/x-www-form-urlencoded"},
                  data={"grant_type": "client_credentials"}).json()
auth = {"Authorization": f"Bearer {tok['access_token']}"}

# 1) 계좌 목록
accs = client.get("/api/v1/accounts", headers=auth)
show("ACCOUNTS  GET /api/v1/accounts", accs)
account_seq = account_no = None
try:
    items = accs.json().get("result", [])
    if items:
        account_seq = str(items[0].get("accountSeq", ""))
        account_no = str(items[0].get("accountNo", ""))
        print(f"\n  -> accountSeq={account_seq}  accountNo={account_no}")
except Exception as e:
    print("계좌 파싱 실패", e)

# 2) 현재가 (계좌 헤더 불필요)
show("PRICE  GET /api/v1/prices?symbols=005930",
     client.get("/api/v1/prices", headers=auth, params={"symbols": "005930"}))

# 3) 잔고 — accountSeq / accountNo 둘 다 시도해서 뭐가 맞는지 판별
for label, val in [("accountSeq", account_seq), ("accountNo", account_no)]:
    if not val:
        continue
    r = client.get("/api/v1/holdings",
                   headers={**auth, "X-Tossinvest-Account": val})
    show(f"HOLDINGS (X-Tossinvest-Account={label}={val})", r)
    if r.status_code == 200:
        print(f"  >>> 헤더에는 '{label}' 가 정답입니다.")
        # 4) 매수가능금액도 같은 헤더로
        show("BUYING-POWER  GET /api/v1/buying-power",
             client.get("/api/v1/buying-power",
                        headers={**auth, "X-Tossinvest-Account": val}))
        break
