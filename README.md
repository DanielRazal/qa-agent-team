# QA Agent Team

A multi-agent system that explores a web application it has never seen, designs test
cases for it, and writes a runnable Playwright suite.

Point it at a URL and it produces working tests:

```bash
python main.py https://practicesoftwaretesting.com --pages 3 --run
```

```
[1/3] EXPLORER    3 pages mapped, 154 selectors verified
[2/3] DESIGNER    13 cases: 10 positive, 1 negative, 2 edge
[3/3] AUTOMATOR   6 files generated
VERIFY            13 passed in 33.14s
```

## Architecture

```
Orchestrator (main.py)
   |
   +-- Explorer   --> crawls with Playwright, extracts elements + stable selectors
   |                  --> output/app_map.json
   +-- Designer   --> turns the map into test cases written in a constrained DSL
   |                  --> output/test_plan.json
   +-- Automator  --> compiles the DSL into Playwright code with Page Objects
   |                  --> tests_generated/
   +-- Triage     --> runs the suite and explains every failure
                      --> output/triage_report.json
```

Agents communicate through JSON files on disk, so every stage is inspectable,
re-runnable, and diffable on its own.

## Design decisions

**The LLM never writes code.** The Designer emits steps in a nine-verb DSL
(`click`, `fill`, `select`, `expect_text`, `expect_disabled`, ...). The Automator
compiles those steps to Playwright deterministically. Generated code is therefore
always syntactically valid — the model contributes test *thinking*, not syntax.

**Selectors are extracted, not invented.** The browser picks each selector by
priority (`data-test*` → `id` → `name` → CSS path) and keeps a candidate only if it
resolves back to exactly that one element. The Designer may only reference selectors
from that verified list; steps pointing anywhere else are dropped before codegen.

**The map records element state.** Elements carry a `disabled` flag that accounts for
wrapper-based disabling (`<li class="disabled">`), not just the HTML attribute. This is
what lets the Designer write *"Previous is disabled on page 1"* instead of clicking a
dead control and timing out.

**Triage argues from evidence, not vibes.** Before the model sees anything, the Triage
agent reruns every failure (a test that passes on rerun is flaky by definition) and
re-checks each locator named in the error against the live page. Two verdicts are then
decided by rule alone, with no model call: `rerun passed -> flaky` and
`selector matches 0 elements -> selector_drift`. The model only explains what the
evidence has not already settled, and groups failures that share a root cause.

**Free-tier first.** Responses are cached on disk keyed by prompt, and the client falls
through a chain of Gemini models when one hits its daily quota. A full run costs three
API calls: one to understand the app, one to design the suite, one to name elements.

## Setup

```bash
python -m venv venv
venv\Scripts\activate            # Windows
pip install -r requirements.txt
playwright install chromium

cp .env.example .env             # then paste a key from aistudio.google.com/apikey
```

## Commands

| Command | What it does |
|---|---|
| `python main.py <url> [--run\|--triage]` | Full pipeline; optionally run or triage the suite |
| `python run_explorer.py <url> [--no-llm]` | Map only — `--no-llm` skips all API calls |
| `python run_designer.py` | Re-design from an existing `app_map.json` |
| `python run_automator.py` | Re-generate code from an existing `test_plan.json` |
| `python run_triage.py` | Run the suite and classify every failure |
| `python verify_map.py` | Check every mapped selector still resolves on the live site |

Useful flags: `--pages N` (crawl depth), `--per-page N` (cases per page),
`--headed` (watch the browser work).

## Generated output

```python
@pytest.mark.medium
@pytest.mark.negative
def test_verify_previous_page_pagination_button_is(page):
    """TC006 - Verify Previous page pagination button is disabled on first page

    Expected: The Previous button is disabled because the user is on page 1.
    """
    po = CategoryHandToolsPage(page)
    po.open()
    assert_disabled(po.pagination_prev, "[data-test=\"pagination-prev\"]")
```

Page Objects are generated alongside the tests, one class per page, with element names
chosen by the model for readability.

## Triage output

```
TRIAGE: 4 failure(s) - {'selector_drift': 2, 'test_defect': 2}

[SELECTOR_DRIFT] (high confidence)  test_homepage.py::test_search_for_products[chromium]
  why      : Locator no longer matches anything: [data-test="search-query-v1"]
  evidence : 1 selector(s) match 0 elements
  action   : Re-run the Explorer to refresh app_map.json, then regenerate.

[FLAKY] (high confidence)  test_homepage.py::test_promo_banner[chromium]
  why      : Passed on rerun with no code change.
  evidence : rerun passed
  action   : Quarantine and investigate the wait or race condition.
```

Triage found a real problem during development: the demo site regenerates its product,
brand and category ids on every reseed, so any test built on `[data-test="product-01KZ…"]`
breaks overnight. The Explorer now flags such elements as `volatile`, and the Designer
is instructed to build tests around stable controls instead.

## Continuous integration

`.github/workflows/qa-agents.yml` runs the whole pipeline nightly on a clean Ubuntu
runner: explore, design, generate, run, triage. The run publishes a job summary, keeps
the generated suite and reports as artifacts for 14 days, and is gated by `ci_gate.py`.

The gate is deliberately narrow. A drifted selector or a flaky test is work for the QA
team, not a broken application, so only two things turn the build red: the pipeline
producing no suite at all, and a failure that Triage classified as a real product bug.

**A green build that proved nothing.** The first CI run passed with three tests named
*"Verify initial security verification page state"*. The runner had been served a
Cloudflare challenge instead of the app, and the agents dutifully mapped the bot wall,
wrote tests for it, and passed them. The Explorer now refuses to map a page that looks
like a bot check (`BlockedError`) rather than generating a suite that tests nothing.

## Limitations

- Read-only crawling: no login, no checkout, nothing that writes data.
- Test quality tracks map quality. A page rendered entirely after user interaction is
  invisible to the crawler.
- Triage reruns a failure once. One rerun proves flakiness when it passes, but cannot
  disprove it when it fails again.
