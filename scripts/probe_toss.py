"""토스 API 실제 경로 탐색. 토큰은 유효하므로 후보 경로들의 상태코드만 확인.
404 edge-blocked = 없는 경로 / 그 외(200,400,401,403) = 경로 존재."""
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


env = load_env(Path(__file__).resolve().parent.parent / ".env")
basic = base64.b64encode(
    f"{env['TOSS_APP_KEY']}:{env['TOSS_APP_SECRET']}".encode()).decode()
client = httpx.Client(base_url=HOST, timeout=15.0)
tok = client.post("/oauth2/token",
                  headers={"Authorization": f"Basic {basic}",
                           "Content-Type": "application/x-www-form-urlencoded"},
                  data={"grant_type": "client_credentials"}).json()
auth = {"Authorization": f"Bearer {tok['access_token']}"}

# 1) 스펙 문서 자동탐색
SPEC_PATHS = [
    "/", "/openapi.json", "/v3/api-docs", "/api-docs", "/swagger.json",
    "/swagger-ui/index.html", "/docs", "/.well-known/openapi",
]
print("##### SPEC DISCOVERY #####")
for p in SPEC_PATHS:
    try:
        r = client.get(p, headers=auth)
        print(f"{r.status_code:>3}  {p}")
        if r.status_code == 200 and ("json" in r.headers.get("content-type", "")
                                     or p.endswith(("json", "docs"))):
            print("     >>", r.text[:400].replace("\n", " "))
    except httpx.HTTPError as e:
        print(f"ERR  {p}  {e}")

# 2) 후보 엔드포인트 경로
CANDIDATES = [
    "/v1/market/price", "/v1/market-data/price", "/v1/quotations/price",
    "/v1/stocks/005930/price", "/v1/stock/price", "/v1/price",
    "/api/v1/market/price", "/v1/market/quote", "/v1/market/stocks/005930",
    "/v1/accounts", "/v1/account", "/v1/account/holdings",
    "/v1/accounts/holding", "/v1/balance", "/v1/portfolio", "/v1/holdings",
    "/v1/order/buying-power", "/v1/orders",
]
print("\n##### ENDPOINT PROBE (404 edge-blocked = 없음) #####")
for p in CANDIDATES:
    try:
        r = client.get(p, headers={**auth,
                                   "X-Tossinvest-Account": env.get("TOSS_ACCOUNT_NO", "")})
        code = None
        try:
            code = r.json().get("error", {}).get("code")
        except Exception:
            pass
        flag = "" if code == "edge-blocked" else "  <-- 존재 가능!"
        print(f"{r.status_code:>3}  {code or 'ok':<14}  {p}{flag}")
    except httpx.HTTPError as e:
        print(f"ERR  {p}  {e}")
