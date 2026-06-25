"""종목 태그로 계좌별 매수가능 판정 데모 (읽기전용).
국내ETF·레버리지ETF·인버스·개별주식·미국ETF를 섞어 분류."""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from bot.eligibility import eligible_accounts  # noqa: E402

HOST = "https://openapi.tossinvest.com"
TEST = [
    "458730",  # TIGER 미국배당다우존스 (국내상장 ETF, 비레버리지)
    "069500",  # KODEX 200 (국내 ETF)
    "122630",  # KODEX 레버리지 (레버리지 ETF)
    "252670",  # KODEX 200선물인버스2X (인버스)
    "005930",  # 삼성전자 (개별주식)
    "SCHD",    # 미국 ETF (해외상장)
    "O",       # 미국 리츠/개별 (해외상장)
]


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

infos = c.get("/api/v1/stocks", headers=auth,
              params={"symbols": ",".join(TEST)}).json()["result"]

print(f"{'종목':<8}{'유형':<14}{'시장':<8}{'lev':<5} → 가능계좌")
print("=" * 70)
for it in infos:
    elig = eligible_accounts(it)
    tags = []
    if "pension" in elig:
        tags.append("연금")
    if "isa" in elig:
        tags.append("ISA")
    tags.append("일반/소수점")
    print(f"{it.get('symbol',''):<8}{str(it.get('securityType','')):<14}"
          f"{str(it.get('market','')):<8}{str(it.get('leverageFactor','')):<5} → "
          f"{', '.join(tags)}   ({it.get('name','')})")
