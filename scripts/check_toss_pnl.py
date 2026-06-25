"""토스 수익률: API가 주는 값 vs 직접 계산 비교."""
import base64
import sys
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from bot.config import settings  # noqa: E402

HOST = "https://openapi.tossinvest.com"
c = httpx.Client(base_url=HOST, timeout=15)
b = base64.b64encode(f"{settings.toss_app_key}:{settings.toss_app_secret}".encode()).decode()
at = c.post("/oauth2/token", headers={"Authorization": f"Basic {b}",
            "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials"}).json()["access_token"]
seq = str(c.get("/api/v1/accounts", headers={"Authorization": f"Bearer {at}"}).json()["result"][0]["accountSeq"])
r = c.get("/api/v1/holdings", headers={"Authorization": f"Bearer {at}", "X-Tossinvest-Account": seq}).json()["result"]

pl = r.get("profitLoss", {})
mv = r.get("marketValue", {})
tp = r.get("totalPurchaseAmount", {})
print("=== 토스 API가 주는 overview ===")
print("  매입총액 USD:", tp.get("usd"))
print("  평가총액 USD:", mv.get("amount", {}).get("usd"))
print("  손익 USD:", pl.get("amount", {}).get("usd"), "| rate:", pl.get("rate"),
      "| rateAfterCost:", pl.get("rateAfterCost"))
print("  amountAfterCost:", (mv.get("amountAfterCost") or {}).get("usd"))

# 직접 계산(종목 기반)
cost = val = 0.0
for it in r.get("items", []):
    q = float(it["quantity"]); a = float(it["averagePurchasePrice"]); p = float(it["lastPrice"])
    cost += q * a; val += q * p
print("\n=== 직접 계산(종목합) ===")
print(f"  매입합 {cost:.2f} / 평가합 {val:.2f} / 손익 {val-cost:.2f} / 수익률 {(val/cost-1)*100:.2f}%")
print(f"\n토스 rate {float(pl.get('rate',0))*100:.2f}% vs 계산 {(val/cost-1)*100:.2f}%")
