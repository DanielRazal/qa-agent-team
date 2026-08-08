"""CLI: python run_designer.py [--per-page N]"""

import argparse
from collections import Counter
from pathlib import Path

from agents.designer import Designer
from core.models import AppMap

parser = argparse.ArgumentParser()
parser.add_argument("--per-page", type=int, default=6)
args = parser.parse_args()

app_map = AppMap.model_validate_json(Path("output/app_map.json").read_text(encoding="utf-8"))
print(f"Designing tests for {len(app_map.pages)} page(s)...")

plan = Designer(per_page=args.per_page).design(app_map)
out = Path("output/test_plan.json")
out.write_text(plan.model_dump_json(indent=2), encoding="utf-8")

kinds = Counter(c.kind for c in plan.cases)
print(f"\n{len(plan.cases)} cases: {dict(kinds)}")
for c in plan.cases:
    print(f"  {c.id} [{c.priority:6}] {c.kind:8} {c.title}")
print(f"\nSaved -> {out}")
