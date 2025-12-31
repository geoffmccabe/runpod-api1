import json
import os
from collections import defaultdict

LORA_DIR = "/workspace/wan-storage/models/loras"
REGISTRY = os.path.join(LORA_DIR, "registry.json")

def load_registry():
    with open(REGISTRY, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["loras"] if isinstance(data, dict) else data

def main():
    entries = load_registry()
    by_cat = defaultdict(list)

    for e in entries:
        cat = e.get("category", "uncategorized")
        by_cat[cat].append(e)

    missing_json = []
    missing_safe = []

    for cat, items in by_cat.items():
        for e in items:
            alias = e.get("alias", "NO_ALIAS")
            fn = e.get("filename", alias + ".safetensors")
            base = os.path.splitext(fn)[0]

            safe_path = os.path.join(LORA_DIR, fn)
            json_path = os.path.join(LORA_DIR, base + ".json")

            if not os.path.isfile(safe_path):
                missing_safe.append((cat, alias, fn))
            if not os.path.isfile(json_path):
                missing_json.append((cat, alias, base + ".json"))

    print("\n=== LoRA SIDE-CAR REPORT ===")
    print(f"Total entries: {len(entries)}")
    print(f"Missing .safetensors: {len(missing_safe)}")
    print(f"Missing .json: {len(missing_json)}")

    def dump(title, rows):
        print(f"\n--- {title} ---")
        for c,a,f in rows:
            print(f"[{c}] {a} -> {f}")

    dump("MISSING SAFETENSORS", missing_safe)
    dump("MISSING JSON", missing_json)

if __name__ == "__main__":
    main()
