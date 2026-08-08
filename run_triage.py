"""CLI: python run_triage.py [--results path.xml] [--no-rerun]

With no --results it runs the generated suite itself, then triages the failures.
"""

import argparse
from collections import Counter
from pathlib import Path

from agents.triage import Triage
from core.models import AppMap

parser = argparse.ArgumentParser()
parser.add_argument("--suite", default="tests_generated")
parser.add_argument("--results", help="existing junit xml instead of running the suite")
parser.add_argument("--no-rerun", action="store_true", help="skip the flakiness rerun")
args = parser.parse_args()

triage = Triage(suite_dir=args.suite)
xml_path = Path(args.results) if args.results else Path("output/results.xml")
xml_path.parent.mkdir(parents=True, exist_ok=True)

if args.results:
    total, failures = triage.parse_junit(xml_path)
else:
    print("Running suite...")
    total, failures = triage.run_suite(xml_path)

print(f"{total - len(failures)}/{total} passed")
if not failures:
    print("Nothing to triage.")
    raise SystemExit(0)

print("\nGathering evidence...")
if not args.no_rerun:
    triage.rerun(failures, xml_path)
app_map = AppMap.model_validate_json(Path("output/app_map.json").read_text(encoding="utf-8"))
triage.check_selectors(failures, app_map)

print("\nClassifying...")
report = triage.analyse(failures, total)
Path("output/triage_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")

print(f"\n{'=' * 70}")
print(f"TRIAGE: {report.failed} failure(s) - {dict(Counter(v.category for v in report.verdicts))}")
print("=" * 70)
for v in report.verdicts:
    print(f"\n[{v.category.upper()}] ({v.confidence} confidence)  {v.test}")
    print(f"  why      : {v.root_cause}")
    print(f"  evidence : {v.evidence}")
    print(f"  action   : {v.action}")

for g in report.groups:
    if len(g.get("tests", [])) > 1:
        print(f"\nShared cause: {g['root_cause']}")
        for t in g["tests"]:
            print(f"   - {t}")

print("\nSaved -> output/triage_report.json")
