import json

spec = json.load(open("scripts/toss_openapi.json", encoding="utf-8"))
schemas = spec["components"]["schemas"]
paths = spec["paths"]

# 1) 주요 operation의 요청/응답 $ref 확인
for method, path in [("get", "/api/v1/prices"), ("get", "/api/v1/holdings"),
                     ("get", "/api/v1/buying-power"), ("post", "/api/v1/orders")]:
    op = paths[path][method]
    rb = op.get("requestBody", {})
    req_ref = ""
    for ct, m in rb.get("content", {}).items():
        req_ref = json.dumps(m.get("schema", {}), ensure_ascii=False)
    resp = op.get("responses", {}).get("200", {})
    res_ref = ""
    for ct, m in resp.get("content", {}).items():
        res_ref = json.dumps(m.get("schema", {}), ensure_ascii=False)
    print(f"{method.upper()} {path}")
    if req_ref:
        print("  REQ :", req_ref)
    print("  RESP:", res_ref)

# 2) 핵심 스키마 원형
for name in ["ApiResponse", "OrderCreateRequest", "BuyingPowerResponse"]:
    print("\n===== " + name + " =====")
    print(json.dumps(schemas.get(name, {}), ensure_ascii=False, indent=1))
