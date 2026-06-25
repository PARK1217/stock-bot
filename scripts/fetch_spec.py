import json
import httpx

URL = "https://openapi.tossinvest.com/openapi-docs/latest/openapi.json"
r = httpx.get(URL, timeout=30)
print("status", r.status_code, "bytes", len(r.content))
spec = r.json()
with open("scripts/toss_openapi.json", "w", encoding="utf-8") as f:
    json.dump(spec, f, ensure_ascii=False)

paths = spec.get("paths", {})
print("TOTAL PATHS", len(paths))
for p in sorted(paths):
    item = paths[p]
    methods = ",".join(m.upper() for m in item if m in
                       ("get", "post", "put", "delete", "patch"))
    summary = ""
    for m in item.values():
        if isinstance(m, dict) and m.get("summary"):
            summary = m["summary"]
            break
    print(f"{methods:<10} {p}   {summary}")
