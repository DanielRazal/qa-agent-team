"""Decide whether the CI run should fail.

Not every red test deserves a red build. A drifted selector or a flaky test is work
for the QA team, not a broken app - so only two things fail the build: the pipeline
not producing a suite at all, and a failure triaged as a real product bug.
"""

import json
from pathlib import Path


def load(name: str):
    path = Path("output") / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


plan, triage = load("test_plan.json"), load("triage_report.json")

if not plan or not plan.get("cases"):
    print("FAIL: the pipeline generated no test cases")
    raise SystemExit(1)

bugs = [v for v in (triage or {}).get("verdicts", []) if v["category"] == "product_bug"]
if bugs:
    print(f"FAIL: {len(bugs)} failure(s) triaged as product bugs")
    for v in bugs:
        print(f"  - {v['test']}: {v['root_cause']}")
    raise SystemExit(1)

other = len((triage or {}).get("verdicts", []))
print(f"PASS: {len(plan['cases'])} case(s) generated" + (f", {other} non-blocking failure(s)" if other else ""))
