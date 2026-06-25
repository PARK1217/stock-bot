import json
s = json.load(open("scripts/toss_openapi.json", encoding="utf-8"))
op = s["paths"]["/api/v1/exchange-rate"]["get"]
print("PARAMS:", [(p.get("name"), p.get("required"),
                   p.get("schema", {}).get("enum")) for p in op.get("parameters", [])])
sc = s["components"]["schemas"]["ExchangeRateResponse"]
print("RESP:", json.dumps(sc.get("properties", sc), ensure_ascii=False))
