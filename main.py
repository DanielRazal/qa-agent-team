"""Orchestrator: Explorer -> Designer -> Automator.

Usage: python main.py <url> [--pages N] [--per-page N] [--run] [--headed]
"""

import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path

from agents.automator import Automator
from agents.designer import Designer
from agents.explorer import Explorer

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
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    banner("[1/3] EXPLORER", f"mapping {args.url}")
    app_map = Explorer(max_pages=args.pages, headless=not args.headed).explore(args.url)
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

    if not args.run:
        print("\nDone. Run the suite with:  cd tests_generated && pytest -v")
        return 0

    banner("VERIFY", "running the generated suite")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-v", "--tb=line"], cwd="tests_generated"
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
