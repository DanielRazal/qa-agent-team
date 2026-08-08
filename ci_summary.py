"""Render the run as GitHub markdown. Safe to call even if the pipeline crashed."""

import json
from collections import Counter
from pathlib import Path

ICON = {
    "product_bug": "🐞",
    "selector_drift": "🎯",
    "flaky": "🎲",
    "test_defect": "🧪",
    "environment": "🌐",
}


def load(name: str):
    path = Path("output") / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


app_map, plan, triage = load("app_map.json"), load("test_plan.json"), load("triage_report.json")

print("## QA Agent Team run\n")

if not app_map:
    print("The Explorer produced no map - the pipeline failed before it could start.")
    raise SystemExit(0)

print(f"**App:** {app_map.get('summary', 'n/a')}\n")
print("| Stage | Result |")
print("| --- | --- |")
print(f"| Explorer | {len(app_map.get('pages', []))} page(s) mapped |")

if plan:
    kinds = Counter(c["kind"] for c in plan.get("cases", []))
    detail = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "none"
    print(f"| Designer | {len(plan.get('cases', []))} case(s): {detail} |")

if triage:
    passed = triage["total"] - triage["failed"]
    print(f"| Suite | {passed}/{triage['total']} passed |")
    print(f"| Triage | {triage['failed']} failure(s) classified |")
else:
    print("| Suite | all tests passed, nothing to triage |")

if not triage or not triage.get("verdicts"):
    raise SystemExit(0)

print("\n### Failures\n")
for v in triage["verdicts"]:
    icon = ICON.get(v["category"], "❓")
    print(f"<details><summary>{icon} <b>{v['category']}</b> ({v['confidence']}) — {v['test']}</summary>\n")
    print(f"- **Why:** {v['root_cause']}")
    print(f"- **Evidence:** {v['evidence']}")
    print(f"- **Action:** {v['action']}\n")
    print("</details>\n")

groups = [g for g in triage.get("groups", []) if len(g.get("tests", [])) > 1]
if groups:
    print("### Shared root causes\n")
    for g in groups:
        print(f"- **{g['root_cause']}** — {len(g['tests'])} tests")
