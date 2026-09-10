import json, collections
rows = collections.Counter()
for line in open("out/chunks.jsonl", encoding="utf-8"):
    d = json.loads(line)
    ph = f"{d.get('phase_no','?')}_{d.get('phase_name','?')}"
    rows[(d.get("project_no",""), ph)] += 1
for (p, ph), n in sorted(rows.items()):
    print(f"{p:14} {ph:24} {n:6}")
