"""Shared data contract between the agents."""

from typing import Literal

from pydantic import BaseModel


class Element(BaseModel):
    kind: str  # button | link | input | select | textarea
    label: str = ""  # accessible name
    css: str = ""  # stable selector
    role: str = ""
    input_type: str = ""
    required: bool = False
    disabled: bool = False  # includes wrappers like <li class="disabled">
    volatile: bool = False  # selector contains a generated id, changes on reseed
    href: str = ""


class Form(BaseModel):
    name: str = ""
    css: str = ""
    fields: list[Element] = []
    submit: Element | None = None


class Page(BaseModel):
    url: str
    title: str = ""
    purpose: str = ""  # filled by the LLM
    actions: list[str] = []  # user actions the LLM spotted
    headings: list[str] = []  # visible h1/h2 text
    elements: list[Element] = []
    forms: list[Form] = []


class AppMap(BaseModel):
    base_url: str
    summary: str = ""
    pages: list[Page] = []

    def selectors(self) -> set[str]:
        """Every selector the app really exposes - used to validate test steps."""
        found = set()
        for page in self.pages:
            found |= {e.css for e in page.elements if e.css}
            for form in page.forms:
                found |= {f.css for f in form.fields if f.css}
                if form.submit and form.submit.css:
                    found.add(form.submit.css)
        return found


# --- test plan ---

# Constrained vocabulary: anything the Designer emits must compile to Playwright.
Action = Literal[
    "goto", "click", "fill", "select", "press",
    "expect_text", "expect_visible", "expect_hidden", "expect_url",
    "expect_disabled", "expect_enabled",
]

NEEDS_SELECTOR = {
    "click", "fill", "select", "press",
    "expect_visible", "expect_hidden", "expect_disabled", "expect_enabled",
}


class Step(BaseModel):
    action: Action
    target: str = ""  # selector from the app map, or a url for goto
    value: str = ""
    note: str = ""


class TestCase(BaseModel):
    id: str
    title: str
    page_url: str
    kind: Literal["positive", "negative", "edge"] = "positive"
    priority: Literal["high", "medium", "low"] = "medium"
    steps: list[Step] = []
    expected: str = ""


class TestPlan(BaseModel):
    base_url: str
    summary: str = ""
    cases: list[TestCase] = []


# --- triage ---


class SelectorCheck(BaseModel):
    selector: str
    matches: int  # 0 = gone, 1 = healthy, >1 = ambiguous


class Failure(BaseModel):
    test: str  # pytest node id
    file: str
    message: str = ""
    rerun_passed: bool | None = None  # None = not rerun
    selector_checks: list[SelectorCheck] = []


Category = Literal["flaky", "selector_drift", "product_bug", "test_defect", "environment"]


class Verdict(BaseModel):
    test: str
    category: Category
    confidence: Literal["high", "medium", "low"] = "medium"
    root_cause: str = ""
    action: str = ""
    evidence: str = ""


class TriageReport(BaseModel):
    total: int = 0
    failed: int = 0
    verdicts: list[Verdict] = []
    groups: list[dict] = []  # {"root_cause": str, "tests": [str]}
