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
]

NEEDS_SELECTOR = {"click", "fill", "select", "press", "expect_visible", "expect_hidden"}


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
