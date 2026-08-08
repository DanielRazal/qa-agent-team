"""Test Designer agent: turns an AppMap into an executable test plan."""

import json

from core.llm import LLM
from core.models import NEEDS_SELECTOR, AppMap, Page, Step, TestCase, TestPlan

SYSTEM = """You are a senior QA engineer designing test cases for a web app.
Think like a tester, not a developer: cover the happy path, then what a user does wrong,
then the boundaries. Prefer few sharp cases over many shallow ones.

You may ONLY use these step actions:
  goto <url>                     - navigate
  click <selector>               - click an element
  fill <selector> <value>        - type into an input
  select <selector> <value>      - pick a dropdown option
  press <selector> <key>         - press a key (e.g. Enter)
  expect_text <text>             - page contains this text
  expect_visible <selector>      - element is visible
  expect_hidden <selector>       - element is not visible
  expect_url <fragment>          - url contains this fragment

HARD RULE: every selector you use MUST be copied exactly from the element list given
to you. Never invent, guess, or modify a selector. If you cannot express a check with
the available selectors, use expect_text instead.
Every test must end with at least one expect_* step."""


# LLMs drift on enum wording - map the usual synonyms back.
_KINDS = {
    "positive": "positive", "happy": "positive", "smoke": "positive", "functional": "positive",
    "negative": "negative", "error": "negative", "invalid": "negative",
    "edge": "edge", "boundary": "edge", "corner": "edge", "limit": "edge",
}
_PRIORITIES = {
    "high": "high", "critical": "high", "p1": "high", "blocker": "high",
    "medium": "medium", "normal": "medium", "p2": "medium",
    "low": "low", "minor": "low", "p3": "low", "trivial": "low",
}


def _norm(value, table: dict, default: str) -> str:
    return table.get(str(value or "").strip().lower(), default)


class Designer:
    def __init__(self, per_page: int = 6, llm: LLM | None = None):
        self.per_page = per_page
        self.llm = llm or LLM()

    def design(self, app_map: AppMap) -> TestPlan:
        plan = TestPlan(base_url=app_map.base_url, summary=app_map.summary)
        known = app_map.selectors()

        for page in app_map.pages:
            print(f"  designing tests for {page.url}")
            try:
                raw = self.llm.ask_json(self._prompt(page, app_map), system=SYSTEM)
            except Exception as e:
                print(f"  ! design failed for {page.url}: {e}")
                continue
            plan.cases += self._parse(raw, page, known)

        for i, case in enumerate(plan.cases, 1):  # renumber across pages
            case.id = f"TC{i:03d}"
        return plan

    def _prompt(self, page: Page, app_map: AppMap) -> str:
        catalog = [
            {"kind": e.kind, "label": e.label, "selector": e.css, "type": e.input_type}
            for e in page.elements
            if e.css and (e.label or e.kind != "link")
        ][:60]
        forms = [
            {
                "form": f.name,
                "fields": [
                    {"label": x.label, "selector": x.css, "type": x.input_type, "required": x.required}
                    for x in f.fields
                ],
                "submit": f.submit.css if f.submit else None,
            }
            for f in page.forms
        ]
        return (
            f"App: {app_map.summary}\n"
            f"Page: {page.title} ({page.url})\n"
            f"Purpose: {page.purpose}\n"
            f"Available user actions: {json.dumps(page.actions, ensure_ascii=False)}\n\n"
            f"Elements you may target:\n{json.dumps(catalog, ensure_ascii=False)}\n\n"
            f"Forms:\n{json.dumps(forms, ensure_ascii=False)}\n\n"
            f"Design up to {self.per_page} test cases for THIS page. "
            "Include at least one negative and one edge case if the page allows it.\n"
            'Return JSON: {"cases": [{"title": "...", "kind": "positive|negative|edge", '
            '"priority": "high|medium|low", "expected": "...", '
            '"steps": [{"action": "...", "target": "...", "value": "...", "note": "..."}]}]}'
        )

    def _parse(self, raw: dict, page: Page, known: set[str]) -> list[TestCase]:
        cases = []
        for item in raw.get("cases", []):
            steps, dropped = self._clean_steps(item.get("steps", []), known)
            if not any(s.action.startswith("expect") for s in steps):
                print(f"    - dropped '{item.get('title')}': no assertion")
                continue
            if dropped:
                print(f"    ~ '{item.get('title')}': dropped {dropped} step(s) with unknown selectors")
            cases.append(
                TestCase(
                    id="",
                    title=item.get("title", "untitled"),
                    page_url=page.url,
                    kind=_norm(item.get("kind"), _KINDS, "positive"),
                    priority=_norm(item.get("priority"), _PRIORITIES, "medium"),
                    expected=item.get("expected", ""),
                    steps=steps,
                )
            )
        return cases

    @staticmethod
    def _clean_steps(raw_steps: list, known: set[str]) -> tuple[list[Step], int]:
        """Keep only steps the Automator can actually compile."""
        steps, dropped = [], 0
        for s in raw_steps:
            try:
                step = Step(**{k: v for k, v in s.items() if k in Step.model_fields})
            except Exception:
                dropped += 1
                continue
            if step.action in NEEDS_SELECTOR and step.target not in known:
                dropped += 1  # hallucinated selector
                continue
            steps.append(step)
        return steps, dropped
