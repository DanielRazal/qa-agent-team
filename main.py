"""Orchestrator: Explorer -> Designer -> Automator -> (Triage).

Usage: python main.py <url> [--pages N] [--per-page N] [--run] [--triage] [--headed]
"""

import argparse
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from agents.automator import Automator
from agents.designer import Designer
from agents.explorer import BlockedError, Explorer
from agents.triage import Triage

OUT = Path("output")


def banner(step: str, title: str) -> None:
    print(f"\n{'=' * 60}\n{step}  {title}\n{'=' * 60}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--pages", type=int, default=5, help="max pages to crawl")
    ap.add_argument("--per-page", type=int, default=5, help="test cases per page")
    ap.add_argument("--headed", action="store_true", help="watch the browser")
    ap.add_argument("--run", action="store_true", help="run the generated suite afterwards")
    ap.add_argument("--triage", action="store_true", help="run the suite and explain failures")
    args = ap.parse_args()

    load_dotenv()
    if not os.getenv("GEMINI_API_KEY"):
        print(
            "GEMINI_API_KEY is not set.\n"
            "  local: copy .env.example to .env and paste a key from "
            "https://aistudio.google.com/apikey\n"
            "  CI:    add it as a repository secret named GEMINI_API_KEY",
            file=sys.stderr,
        )
        return 2

    OUT.mkdir(exist_ok=True)
    for stale in ("triage_report.json", "results.xml", "rerun.xml"):
        (OUT / stale).unlink(missing_ok=True)  # never report last run's failures

    banner("[1/3] EXPLORER", f"mapping {args.url}")
    try:
        app_map = Explorer(max_pages=args.pages, headless=not args.headed).explore(args.url)
    except BlockedError as e:
        print(f"\nBLOCKED: {e}", file=sys.stderr)
        return 3
    (OUT / "app_map.json").write_text(app_map.model_dump_json(indent=2), encoding="utf-8")
    print(f"\n{app_map.summary}")
    print(f"-> {len(app_map.pages)} page(s) mapped")

    banner("[2/3] DESIGNER", "writing test cases")
    plan = Designer(per_page=args.per_page).design(app_map)
    (OUT / "test_plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    if not plan.cases:
        print("No test cases produced - stopping.")
        return 1
    print(f"\n-> {len(plan.cases)} case(s): {dict(Counter(c.kind for c in plan.cases))}")
    for c in plan.cases:
        print(f"   {c.id} [{c.priority:6}] {c.kind:8} {c.title}")

    banner("[3/3] AUTOMATOR", "generating Playwright code")
    files = Automator().generate(plan, app_map)
    for f in files:
        print(f"   + {f}")

    if args.triage:
        banner("[4/4] TRIAGE", "running the suite and explaining failures")
        return run_triage(app_map)

    if not args.run:
        print("\nDone. Run the suite with:  cd tests_generated && pytest -v")
        return 0

    banner("VERIFY", "running the generated suite")
    pytest_args = ["-v", "--tb=line"]
    if args.headed:  # watch the suite the way you watched the crawl
        pytest_args += ["--headed", "--slowmo", "400"]
    result = subprocess.run([sys.executable, "-m", "pytest", *pytest_args], cwd="tests_generated")
    return result.returncode


def run_triage(app_map) -> int:
    triage = Triage()
    xml_path = OUT / "results.xml"
    total, failures = triage.run_suite(xml_path)
    print(f"{total - len(failures)}/{total} passed")
    if not failures:
        print("Nothing to triage.")
        return 0

    triage.rerun(failures, xml_path)
    triage.check_selectors(failures, app_map)
    report = triage.analyse(failures, total)
    (OUT / "triage_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")

    print(f"\n{len(report.verdicts)} verdict(s): {dict(Counter(v.category for v in report.verdicts))}")
    for v in report.verdicts:
        print(f"\n[{v.category.upper()}] ({v.confidence}) {v.test}")
        print(f"  why    : {v.root_cause}")
        print(f"  action : {v.action}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
