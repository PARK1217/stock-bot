import json

spec = json.load(open("scripts/toss_openapi.json", encoding="utf-8"))
schemas = spec.get("components", {}).get("schemas", {})


def props(name, depth=0, seen=None):
    seen = seen or set()
    if name in seen or depth > 3:
        print("  " * depth + f"{name} ...")
        return
    seen = seen | {name}
    s = schemas.get(name, {})
    p = s.get("properties", {})
    if not p:
        # allOf 등
        for sub in s.get("allOf", []) + s.get("oneOf", []):
            if "$ref" in sub:
                props(sub["$ref"].split("/")[-1], depth, seen)
        return
    for k, v in p.items():
        t = v.get("type", "")
        ref = None
        if "$ref" in v:
            ref = v["$ref"].split("/")[-1]
            t = ref
        if v.get("type") == "array":
            it = v.get("items", {})
            if "$ref" in it:
                ref = it["$ref"].split("/")[-1]
                t = f"array<{ref}>"
            else:
                t = f"array<{it.get('type','')}>"
        print("  " * depth + f"{k}: {t}")
        if ref:
            props(ref, depth + 1, seen)


# 관심 컴포넌트만 추려서 출력
keys = list(schemas)
print("ALL SCHEMA NAMES:")
for k in keys:
    print("  ", k)

WANT = [k for k in keys if any(w in k.lower() for w in
        ("price", "holding", "account", "buyingpower", "buying", "order"))]
for name in WANT:
    print("\n" + "=" * 60)
    print("SCHEMA:", name)
    props(name)
