"""실계좌 데이터로 리밸런싱 '제안' 미리보기 (읽기 전용, 주문/DB 없음).
dividend_core 의 비중 계산식과 동일한 로직으로 실제 숫자를 보여준다."""
from __future__ import annotations

import base64
from pathlib import Path

import httpx

HOST = "https://openapi.tossinvest.com"
BASKET = [  # (symbol, market, weight)
    ("O", "US", 0.20), ("SCHD", "US", 0.25), ("JEPI", "US", 0.15),
    ("161510", "KR", 0.20), ("069500", "KR", 0.20),
]
BAND = 0.05


def load_env(p):
    env = {}
    for line in Path(p).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.split(" #")[0].strip()
    return env


env = load_env(Path(__file__).resolve().parent.parent / ".env")
c = httpx.Client(base_url=HOST, timeout=15.0)
basic = base64.b64encode(f"{env['TOSS_APP_KEY']}:{env['TOSS_APP_SECRET']}".encode()).decode()
tok = c.post("/oauth2/token", headers={"Authorization": f"Basic {basic}",
             "Content-Type": "application/x-www-form-urlencoded"},
             data={"grant_type": "client_credentials"}).json()["access_token"]
auth = {"Authorization": f"Bearer {tok}"}
seq = str(c.get("/api/v1/accounts", headers=auth).json()["result"][0]["accountSeq"])
acc = {**auth, "X-Tossinvest-Account": seq}

fx = float(c.get("/api/v1/exchange-rate", headers=auth,
                 params={"baseCurrency": "USD", "quoteCurrency": "KRW"}).json()["result"]["rate"])
cash = float(c.get("/api/v1/buying-power", headers=acc,
                   params={"currency": "KRW"}).json()["result"]["cashBuyingPower"])
hold = c.get("/api/v1/holdings", headers=acc).json()["result"]["items"]
held = {h["symbol"]: h for h in hold}

syms = ",".join(s for s, _, _ in BASKET)
pr = c.get("/api/v1/prices", headers=auth, params={"symbols": syms}).json()["result"]
prices = {p["symbol"]: float(p["lastPrice"]) for p in pr}


def krw(h):
    v = float(h["quantity"]) * float(h["lastPrice"])
    return v * (fx if h.get("currency") == "USD" else 1)


total = cash + sum(krw(h) for h in hold)
print(f"USDKRW={fx:,.1f}  현금={cash:,.0f}KRW  총평가(원화)={total:,.0f}KRW\n")
print("현재 보유:")
for h in hold:
    w = krw(h) / total
    print(f"  {h['symbol']:<7}{h.get('currency'):<4} {float(h['quantity']):g}주  "
          f"원화환산 {krw(h):,.0f}  비중 {w:.1%}")

print("\n=== 제안(미리보기) ===")
any_sig = False
for sym, market, weight in BASKET:
    price = prices.get(sym)
    if not price:
        print(f"  {sym}: 시세 없음(스킵)")
        continue
    f = fx if market == "US" else 1.0
    cur = krw(held[sym]) if sym in held else 0.0
    w = cur / total
    if abs(w - weight) < BAND:
        print(f"  {sym}: 비중 {w:.1%} (목표 {weight:.0%}) → 밴드내 유지")
        continue
    diff_krw = weight * total - cur
    qty = diff_krw / f / price
    qty = int(qty) if market == "KR" else round(qty, 6)
    if qty == 0:
        continue
    side = "BUY" if diff_krw > 0 else "SELL"
    any_sig = True
    print(f"  >> {side} {sym} {abs(qty):g}주 @~{price:,.2f}"
          f"{'USD' if market=='US' else 'KRW'}  (비중 {w:.1%}->{weight:.0%})")
if not any_sig:
    print("  (없음)")
