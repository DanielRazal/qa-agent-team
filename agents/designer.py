"""Test Designer agent: turns an AppMap into an executable test plan."""

import json

from core.llm import LLM, unwrap
from core.models import NEEDS_SELECTOR, AppMap, Page, Step, TestCase, TestPlan

SYSTEM = """You are a senior QA engineer designing test cases for a web app.
Think like a tester, not a developer: cover the happy path, then what a user does wrong,
then the boundaries. Prefer few sharp cases over many shallow ones.

You may ONLY use these step actions:
  goto <url>                     - navigate
  click <selector>               - click an element
  fill <selector> <value>        - type into an input
  select <selector> <value>      - pick a dropdown option, using a listed option value
  press <selector> <key>         - press a key (e.g. Enter)
  expect_text <text>             - page contains this text
  expect_visible <selector>      - element is visible
  expect_hidden <selector>       - element is not visible
  expect_disabled <selector>     - element is disabled
  expect_enabled <selector>      - element is enabled
  expect_url <fragment>          - url contains this fragment

HARD RULES
1. Every selector MUST be copied exactly from the element list given to you.
   Never invent, guess, or modify a selector. If you cannot express a check with the
   available selectors, use expect_text instead.
2. Elements marked "disabled": true cannot be clicked. To test that boundary assert
   expect_disabled on them - never click them, and never expect a click to do anything.
3. After acting on an element, do not assert that the same element is still visible.
   Clicking often replaces or hides it. Assert the RESULT instead: new text, new url.
4. expect_text must use text a user can SEE on the page - copy it from the element
   labels or headings you were given. The browser tab title is not page text.
5. Elements marked "volatile": true carry generated ids that change every time the
   app's data is reseeded. Prefer stable elements. Use a volatile one only when the
   test is impossible without it, and assert with expect_text rather than its selector.
6. expect_url and goto may only use a url or fragment that appears in the urls you were
   given. Filtering and sorting often happen without changing the url at all - never
   invent a query string like "?sort=price" to assert on.
7. A select step must use one of the values listed in that element's "options".
8. Every test must end with at least one expect_* step."""


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
        """One LLM call for the whole app - the free tier has a daily request cap."""
        plan = TestPlan(base_url=app_map.base_url, summary=app_map.summary)
        known, known_urls = app_map.selectors(), app_map.urls()
        volatile = app_map.volatile_selectors()
        options = app_map.options_by_selector()
        by_url = {p.url: p for p in app_map.pages}

        print(f"  designing tests for {len(app_map.pages)} page(s) in one call")
        try:
            raw = self.llm.ask_json(self._prompt(app_map), system=SYSTEM)
        except Exception as e:
            print(f"  ! design failed: {e}")
            return plan

        for group in unwrap(raw, "pages").get("pages", []):
            page = by_url.get(group.get("url", ""))
            if not page:
                print(f"    - unknown page in response: {group.get('url')}")
                continue
            plan.cases += self._parse(group, page, known, known_urls, volatile, options)

        for i, case in enumerate(plan.cases, 1):  # renumber across pages
            case.id = f"TC{i:03d}"
        return plan

    def _prompt(self, app_map: AppMap) -> str:
        pages = [
            {
                "url": p.url,
                "title": p.title,
                "purpose": p.purpose,
                "user_actions": p.actions,
                "visible_headings": p.headings,
                "elements": [
                    {"kind": e.kind, "label": e.label, "selector": e.css,
                     "type": e.input_type, "disabled": e.disabled, "volatile": e.volatile,
                     **({"options": [o.value for o in e.options]} if e.options else {})}
                    for e in p.elements
                    if e.css and (e.label or e.kind != "link")
                ][:45],
                "forms": [
                    {
                        "form": f.name,
                        "fields": [
                            {"label": x.label, "selector": x.css, "type": x.input_type,
                             "required": x.required}
                            for x in f.fields
                        ],
                        "submit": f.submit.css if f.submit else None,
                    }
                    for f in p.forms
                ],
            }
            for p in app_map.pages
        ]
        return (
            f"App: {app_map.summary}\n\n"
            f"Pages:\n{json.dumps(pages, ensure_ascii=False)}\n\n"
            f"Design up to {self.per_page} test cases PER PAGE. "
            "Across the suite include negative and edge cases, not just happy paths.\n"
            'Return JSON: {"pages": [{"url": "<exact url>", "cases": [{"title": "...", '
            '"kind": "positive|negative|edge", "priority": "high|medium|low", "expected": "...", '
            '"steps": [{"action": "...", "target": "...", "value": "...", "note": "..."}]}]}]}'
        )

    def _parse(
        self, group: dict, page: Page, known: set[str], known_urls: set[str],
        volatile: set[str], options: dict[str, set[str]],
    ) -> list[TestCase]:
        cases = []
        for item in group.get("cases", []):
            steps, dropped = self._clean_steps(item.get("steps", []), known, known_urls, options)
            rotting = [s.target for s in steps if s.target in volatile]
            if rotting:
                # a whole case, not just the step: the remainder would assert nonsense
                print(f"    - dropped '{item.get('title')}': built on a generated id ({rotting[0]})")
                continue
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
    def _clean_steps(
        raw_steps: list, known: set[str], known_urls: set[str], options: dict[str, set[str]]
    ) -> tuple[list[Step], int]:
        """Keep only steps the Automator can actually compile against a real app."""
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
            if step.action in ("expect_url", "goto"):
                fragment = step.value or step.target
                if not fragment or not any(fragment in url for url in known_urls):
                    dropped += 1  # invented route or query string
                    continue
            if step.action == "select" and step.value not in options.get(step.target, set()):
                dropped += 1  # option that the dropdown does not offer
                continue
            steps.append(step)
        return steps, dropped
