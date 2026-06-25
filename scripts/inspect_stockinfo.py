import json
s = json.load(open("scripts/toss_openapi.json", encoding="utf-8"))
sch = s["components"]["schemas"]

# /api/v1/stocks 파라미터
op = s["paths"]["/api/v1/stocks"]["get"]
print("== GET /api/v1/stocks params ==")
for p in op.get("parameters", []):
    nm = p.get("name") or p.get("$ref", "")
    print(" ", nm, p.get("required"), p.get("schema", {}).get("type"),
          "::", (p.get("description") or "")[:70])

for name in ["StockInfo", "KrMarketDetail"]:
    print(f"\n== {name} ==")
    print(json.dumps(sch.get(name, {}).get("properties", sch.get(name, {})),
                     ensure_ascii=False, indent=1)[:2500])
