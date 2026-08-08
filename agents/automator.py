"""Automator agent: compiles a TestPlan into runnable Playwright pytest code.

Step -> code translation is deterministic; the LLM only names things.
"""

import json
import keyword
import re
from pathlib import Path

from core.llm import LLM, unwrap
from core.models import AppMap, Page, TestCase, TestPlan

NAMING_SYSTEM = """You name UI elements for a Page Object Model.
Given selectors with their visible labels, return a short snake_case Python
attribute name for each: role first, e.g. search_input, submit_button,
sort_dropdown, product_grid. Keep names under 30 characters and unique."""

CONFTEST = '''"""Shared fixtures for the generated suite."""

import pytest
from playwright.sync_api import expect

expect.set_options(timeout=10_000)


@pytest.fixture(autouse=True)
def _timeouts(page):
    page.set_default_timeout(10_000)
    yield
'''

HELPERS = '''"""Assertions that match how the Explorer decides an element is disabled."""

from playwright.sync_api import expect

_DISABLED_JS = """e => !!(e.disabled
    || e.getAttribute('aria-disabled') === 'true'
    || e.closest('.disabled, [disabled], [aria-disabled="true"]'))"""


def _is_disabled(locator) -> bool:
    element = locator.first
    expect(element).to_be_attached()
    return element.evaluate(_DISABLED_JS)


def assert_disabled(locator, name: str = "element") -> None:
    assert _is_disabled(locator), f"{name} should be disabled but is not"


def assert_enabled(locator, name: str = "element") -> None:
    assert not _is_disabled(locator), f"{name} should be enabled but is disabled"
'''

PYTEST_INI = """[pytest]
markers =
    high: high priority
    medium: medium priority
    low: low priority
    positive: happy path
    negative: invalid input
    edge: boundary case
"""


def _slug(text: str, fallback: str = "item") -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    s = re.sub(r"_+", "_", s)
    if not s or s[0].isdigit():
        s = f"{fallback}_{s}" if s else fallback
    return s[:40] if not keyword.iskeyword(s) else f"{s}_"


def _fn_name(title: str) -> str:
    """Readable test name - trimmed on a word boundary, not mid-word."""
    s = _slug(title, "case")
    if len(s) >= 40 and "_" in s:
        s = s.rsplit("_", 1)[0]
    return s


def _class_name(page: Page) -> str:
    path = re.sub(r"^https?://[^/]+", "", page.url).strip("/")
    base = _slug(path.replace("/", " ") or "home")
    return "".join(w.capitalize() for w in base.split("_")) + "Page"


def _lit(value: str) -> str:
    """Safe Python string literal."""
    return json.dumps(value, ensure_ascii=False)


class Automator:
    def __init__(self, out_dir: str = "tests_generated", llm: LLM | None = None):
        self.out = Path(out_dir)
        self.llm = llm or LLM()

    def _clear_previous(self) -> None:
        """Stale files from an earlier run would otherwise still be collected."""
        for pattern in ("test_*.py", "pages/*.py", "helpers.py"):
            for path in self.out.glob(pattern):
                path.unlink()
        for cache in self.out.rglob("__pycache__"):
            for path in cache.glob("*"):
                path.unlink()
            cache.rmdir()

    def generate(self, plan: TestPlan, app_map: AppMap) -> list[Path]:
        (self.out / "pages").mkdir(parents=True, exist_ok=True)
        self._clear_previous()
        (self.out / "conftest.py").write_text(CONFTEST, encoding="utf-8")
        (self.out / "pytest.ini").write_text(PYTEST_INI, encoding="utf-8")
        (self.out / "helpers.py").write_text(HELPERS, encoding="utf-8")
        (self.out / "pages" / "__init__.py").write_text("", encoding="utf-8")

        active = [
            (page, [c for c in plan.cases if c.page_url == page.url]) for page in app_map.pages
        ]
        active = [(p, c) for p, c in active if c]

        names_by_page = self._name_all(active)
        written = []
        for page, cases in active:
            names = names_by_page[page.url]
            written.append(self._write_page_object(page, names))
            written.append(self._write_tests(page, cases, names))
        return written

    def _referenced(self, page: Page, cases: list[TestCase]) -> dict[str, str]:
        """Selectors the tests actually use, mapped to their label."""
        labels = {e.css: e.label for e in page.elements if e.css}
        for form in page.forms:
            labels |= {f.css: f.label for f in form.fields if f.css}
            if form.submit and form.submit.css:
                labels[form.submit.css] = form.submit.label
        used = {s.target for c in cases for s in c.steps if s.target in labels}
        return {css: labels[css] for css in used}

    @staticmethod
    def _fallback_names(refs: dict[str, str]) -> dict[str, str]:
        """Names derived from labels - used when the LLM is unavailable."""
        out: dict[str, str] = {}
        for css, label in refs.items():
            base = _slug(label) or _slug(re.sub(r'[\[\]"=#]', " ", css), "element")
            name, n = base, 2
            while name in out.values():
                name, n = f"{base}_{n}", n + 1
            out[css] = name
        return out

    def _name_all(self, active: list[tuple[Page, list[TestCase]]]) -> dict[str, dict[str, str]]:
        """One LLM call names the elements of every page."""
        refs_by_page = {page.url: self._referenced(page, cases) for page, cases in active}
        fallback = {url: self._fallback_names(refs) for url, refs in refs_by_page.items()}
        if not any(refs_by_page.values()):
            return fallback

        payload = [
            {
                "url": page.url,
                "page": page.title,
                "elements": [
                    {"selector": css, "label": label}
                    for css, label in refs_by_page[page.url].items()
                ],
            }
            for page, _ in active
        ]
        prompt = (
            f"{json.dumps(payload, ensure_ascii=False)}\n\n"
            'Return JSON: {"pages": [{"url": "<exact url>", "names": '
            '[{"selector": "<exact selector>", "name": "<snake_case>"}]}]}'
        )
        try:
            raw = self.llm.ask_json(prompt, system=NAMING_SYSTEM)
        except Exception as e:
            print(f"  ! naming failed, falling back to labels: {e}")
            return fallback

        result = {}
        for group in unwrap(raw, "pages").get("pages", []):
            url = group.get("url", "")
            if url not in refs_by_page:
                continue
            refs, names, taken = refs_by_page[url], {}, set()
            for item in group.get("names", []):
                css, name = item.get("selector", ""), _slug(item.get("name", ""))
                if css in refs and name and name not in taken:
                    names[css], _ = name, taken.add(name)
            for css, name in fallback[url].items():  # fill any the LLM skipped
                if css not in names:
                    while name in taken:
                        name += "_x"
                    names[css], _ = name, taken.add(name)
            result[url] = names
        return {url: result.get(url, fallback[url]) for url in refs_by_page}

    def _write_page_object(self, page: Page, names: dict[str, str]) -> Path:
        cls = _class_name(page)
        lines = [
            '"""Generated Page Object - do not edit by hand."""',
            "",
            "from playwright.sync_api import Page",
            "",
            "",
            f"class {cls}:",
            f"    URL = {_lit(page.url)}",
            "",
            "    def __init__(self, page: Page):",
            "        self.page = page",
        ]
        for css, name in sorted(names.items(), key=lambda kv: kv[1]):
            lines.append(f"        self.{name} = page.locator({_lit(css)})")
        lines += [
            "",
            "    def open(self):",
            "        self.page.goto(self.URL)",
            "        return self",
            "",
        ]
        path = self.out / "pages" / f"{_slug(cls)}.py"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def _write_tests(self, page: Page, cases: list[TestCase], names: dict[str, str]) -> Path:
        cls = _class_name(page)
        module = _slug(cls)
        var = "po"
        state_checks = sorted(
            {s.action for c in cases for s in c.steps if s.action in ("expect_disabled", "expect_enabled")}
        )
        lines = [
            f'"""Generated tests for: {page.title}"""',
            "",
            "import re",
            "",
            "import pytest",
            "from playwright.sync_api import expect",
            "",
        ]
        if state_checks:
            helpers = ", ".join(a.replace("expect_", "assert_") for a in state_checks)
            lines.append(f"from helpers import {helpers}")
        lines += [f"from pages.{module} import {cls}", ""]
        used_names = set()
        for case in cases:
            fn = _fn_name(case.title)
            while fn in used_names:
                fn += "_2"
            used_names.add(fn)
            lines += [
                "",
                "",
                f"@pytest.mark.{case.priority}",
                f"@pytest.mark.{case.kind}",
                f"def test_{fn}(page):",
                f'    """{case.id} - {case.title}',
                "",
                f"    Expected: {case.expected}",
                '    """',
                f"    {var} = {cls}(page)",
                f"    {var}.open()",
            ]
            body = [self._compile(step, names, var, page.url) for step in case.steps]
            lines += [f"    {line}" for line in body if line]
        lines.append("")
        path = self.out / f"test_{module}.py"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    @staticmethod
    def _compile(step, names: dict[str, str], var: str, page_url: str) -> str:
        """One DSL step -> one line of Playwright."""
        target, value = step.target, step.value
        loc = f"{var}.{names[target]}" if target in names else f"page.locator({_lit(target)})"
        note = f"  # {step.note}" if step.note else ""

        match step.action:
            case "goto":
                if not target or target.rstrip("/") == page_url.rstrip("/"):
                    return ""  # open() already did it
                return f"page.goto({_lit(target)}){note}"
            case "click":
                return f"{loc}.click(){note}"
            case "fill":
                return f"{loc}.fill({_lit(value)}){note}"
            case "select":
                return f"{loc}.select_option({_lit(value)}){note}"
            case "press":
                return f"{loc}.press({_lit(value or 'Enter')}){note}"
            case "expect_text":
                # filter(visible=True): the same text often sits in a collapsed menu too
                text = value or target
                return (
                    f"expect(page.get_by_text({_lit(text)})"
                    f".filter(visible=True).first).to_be_visible(){note}"
                )
            case "expect_visible":
                return f"expect({loc}.first).to_be_visible(){note}"
            case "expect_hidden":
                return f"expect({loc}.first).to_be_hidden(){note}"
            case "expect_disabled":
                return f"assert_disabled({loc}, {_lit(target)}){note}"
            case "expect_enabled":
                return f"assert_enabled({loc}, {_lit(target)}){note}"
            case "expect_url":
                frag = value or target
                return f"expect(page).to_have_url(re.compile({_lit(re.escape(frag))})){note}"
        return ""
