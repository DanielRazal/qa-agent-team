"""CLI: python run_automator.py"""

from pathlib import Path

from agents.automator import Automator
from core.models import AppMap, TestPlan

app_map = AppMap.model_validate_json(Path("output/app_map.json").read_text(encoding="utf-8"))
plan = TestPlan.model_validate_json(Path("output/test_plan.json").read_text(encoding="utf-8"))

print(f"Generating code for {len(plan.cases)} test case(s)...")
files = Automator().generate(plan, app_map)

for f in files:
    print(f"  + {f}")
print("\nRun them with:  cd tests_generated && pytest -v")
