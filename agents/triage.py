"""Triage agent: decides WHY tests failed, using evidence rather than guesswork.

Evidence gathered before any model call:
  1. rerun the failures - a test that passes on a rerun is flaky, by definition
  2. re-check every selector named in the error against the live page
The model then explains and groups; hard evidence overrides its opinion.
"""

import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from playwright.sync_api import sync_playwright

from agents.automator import _class_name, _slug
from core.llm import LLM, unwrap
from core.models import AppMap, Failure, SelectorCheck, TriageReport, Verdict

SYSTEM = """You are a QA lead triaging a failed test run.
For each failure decide ONE category:
  product_bug     - the app behaves wrongly; the test is right
  test_defect     - the test asserts something wrong or unrealistic
  selector_drift  - the UI moved; the locator no longer matches
  flaky           - passes and fails without any code change (timing, races)
  environment     - network, timeout, infrastructure, not the app logic

You are given EVIDENCE. Evidence outweighs your intuition:
  rerun_passed=true          -> flaky, high confidence
  a selector with matches=0  -> selector_drift
  a selector with matches>1  -> the locator is ambiguous, a test_defect
  all selectors matches=1    -> the elements exist, so look at behaviour or assertions
Then group failures that share one root cause."""

_LOCATOR_RE = re.compile(r'locator\("((?:[^"\\]|\\.)*)"\)')


class Triage:
    def __init__(self, suite_dir: str = "tests_generated", llm: LLM | None = None):
        self.suite = Path(suite_dir)
        self.llm = llm

    # --- collect ---

    @staticmethod
    def parse_junit(xml_path: Path) -> tuple[int, list[Failure]]:
        root = ET.parse(xml_path).getroot()
        total, failures = 0, []
        for case in root.iter("testcase"):
            total += 1
            problem = case.find("failure") if case.find("failure") is not None else case.find("error")
            if problem is None:
                continue
            file = case.get("file") or f"{case.get('classname', '').split('.')[-1]}.py"
            message = (problem.get("message") or "") + "\n" + (problem.text or "")
            failures.append(Failure(test=f"{file}::{case.get('name')}", file=file, message=message))
        return total, failures

    def run_suite(self, xml_path: Path) -> tuple[int, list[Failure]]:
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", f"--junitxml={xml_path.resolve()}"],
            cwd=self.suite,
            capture_output=True,
        )
        return self.parse_junit(xml_path)

    # --- evidence ---

    def rerun(self, failures: list[Failure], xml_path: Path) -> None:
        """A failure that passes on a second run is flaky - proven, not guessed."""
        if not failures:
            return
        print(f"  rerunning {len(failures)} failure(s) to check for flakiness")
        rerun_xml = xml_path.with_name("rerun.xml")
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", f"--junitxml={rerun_xml.resolve()}"]
            + [f.test for f in failures],
            cwd=self.suite,
            capture_output=True,
        )
        if not rerun_xml.exists():
            return
        _, still_failing = self.parse_junit(rerun_xml)
        failed_again = {f.test for f in still_failing}
        for failure in failures:
            failure.rerun_passed = failure.test not in failed_again

    def check_selectors(self, failures: list[Failure], app_map: AppMap) -> None:
        """Ask the live page whether the locators in the error still exist."""
        by_file = {f"test_{_slug(_class_name(p))}.py": p.url for p in app_map.pages}
        wanted: dict[str, set[str]] = {}
        for failure in failures:
            url = by_file.get(failure.file)
            selectors = {m.replace('\\"', '"') for m in _LOCATOR_RE.findall(failure.message)}
            if url and selectors:
                wanted.setdefault(url, set()).update(selectors)
        if not wanted:
            return

        print(f"  re-checking selectors on {len(wanted)} page(s)")
        counts: dict[tuple[str, str], int] = {}
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            for url, selectors in wanted.items():
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                for css in selectors:
                    try:
                        counts[(url, css)] = page.locator(css).count()
                    except Exception:
                        counts[(url, css)] = -1  # invalid selector
            browser.close()

        for failure in failures:
            url = by_file.get(failure.file)
            for css in {m.replace('\\"', '"') for m in _LOCATOR_RE.findall(failure.message)}:
                if (url, css) in counts:
                    failure.selector_checks.append(
                        SelectorCheck(selector=css, matches=counts[(url, css)])
                    )

    # --- verdicts ---

    @staticmethod
    def rule_verdict(failure: Failure) -> Verdict | None:
        """Evidence strong enough to decide without a model."""
        if failure.rerun_passed:
            return Verdict(
                test=failure.test,
                category="flaky",
                confidence="high",
                root_cause="Passed on rerun with no code change.",
                action="Quarantine and investigate the wait or race condition.",
                evidence="rerun passed",
            )
        gone = [c.selector for c in failure.selector_checks if c.matches == 0]
        if gone:
            return Verdict(
                test=failure.test,
                category="selector_drift",
                confidence="high",
                root_cause=f"Locator no longer matches anything: {gone[0]}",
                action="Re-run the Explorer to refresh app_map.json, then regenerate.",
                evidence=f"{len(gone)} selector(s) match 0 elements",
            )
        return None

    def analyse(self, failures: list[Failure], total: int) -> TriageReport:
        report = TriageReport(total=total, failed=len(failures))
        undecided = []
        for failure in failures:
            verdict = self.rule_verdict(failure)
            if verdict:
                report.verdicts.append(verdict)
            else:
                undecided.append(failure)

        if undecided:
            report.verdicts += self._ask_model(undecided, report)
        report.verdicts.sort(key=lambda v: v.category)
        return report

    def _ask_model(self, failures: list[Failure], report: TriageReport) -> list[Verdict]:
        payload = [
            {
                "test": f.test,
                "error": f.message.strip()[:1200],
                "rerun_passed": f.rerun_passed,
                "selectors": [{"selector": c.selector, "matches": c.matches} for c in f.selector_checks],
            }
            for f in failures
        ]
        prompt = (
            f"Failed tests with evidence:\n{json.dumps(payload, ensure_ascii=False)}\n\n"
            'Return JSON: {"verdicts": [{"test": "<exact test id>", "category": "...", '
            '"confidence": "high|medium|low", "root_cause": "<one sentence>", '
            '"action": "<what a QA engineer should do next>", "evidence": "<what decided it>"}], '
            '"groups": [{"root_cause": "<shared cause>", "tests": ["<test id>"]}]}'
        )
        try:
            raw = (self.llm or LLM()).ask_json(prompt, system=SYSTEM)
        except Exception as e:
            print(f"  ! model triage unavailable ({e}) - reporting evidence only")
            return [
                Verdict(
                    test=f.test,
                    category="product_bug",
                    confidence="low",
                    root_cause="Not classified: no model available.",
                    action="Review the error manually.",
                    evidence=self._summarise(f),
                )
                for f in failures
            ]

        raw = unwrap(raw, "verdicts")
        report.groups = raw.get("groups", [])
        known = {f.test for f in failures}
        verdicts = []
        for item in raw.get("verdicts", []):
            if item.get("test") in known:
                try:
                    verdicts.append(Verdict(**item))
                except Exception:
                    continue
        return verdicts

    @staticmethod
    def _summarise(failure: Failure) -> str:
        checks = ", ".join(f"{c.selector}={c.matches}" for c in failure.selector_checks)
        return f"rerun_passed={failure.rerun_passed}; selectors: {checks or 'none found'}"
