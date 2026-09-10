import json, collections, re
PROJ = re.compile(r"^[AR]\d{5,8}")
man = json.load(open("out/manifest.json", encoding="utf-8"))
agg = collections.defaultdict(collections.Counter)
for r in man:
    parts = str(r.get("source_path","")).replace("\\","/").split("/")
    i = next((k for k,p in enumerate(parts) if PROJ.match(p)), None)
    if i is None: continue
    phase = parts[i+1] if len(parts) > i+1 else "(direkt im Projekt)"
    agg[(parts[i], phase)][r.get("status","?")] += 1
for k in sorted(agg):
    print(f"{k[0]:12} {k[1]:22} " + "  ".join(f"{s}={n}" for s,n in agg[k].most_common()))
