import json
s = json.load(open("scripts/toss_openapi.json", encoding="utf-8"))
op = s["paths"]["/api/v1/candles"]["get"]
for p in op.get("parameters", []):
    print("PARAM", p.get("name"), "req" if p.get("required") else "opt",
          p.get("schema", {}).get("enum") or p.get("schema", {}).get("type"),
          "::", (p.get("description") or "")[:60])
print("CANDLE", json.dumps(s["components"]["schemas"]["Candle"].get("properties", {}), ensure_ascii=False))
