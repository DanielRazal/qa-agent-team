"""Selector health check: does every selector in app_map.json still resolve?

Catches both extraction bugs and real UI drift. No LLM calls.
Usage: python verify_map.py
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

from core.models import AppMap

app_map = AppMap.model_validate_json(Path("output/app_map.json").read_text(encoding="utf-8"))

total = broken = ambiguous = 0
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    for mapped in app_map.pages:
        page.goto(mapped.url, wait_until="domcontentloaded", timeout=20000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        selectors = {e.css for e in mapped.elements if e.css}
        for form in mapped.forms:
            selectors |= {f.css for f in form.fields if f.css}
        bad = []
        for css in sorted(selectors):
            total += 1
            count = page.locator(css).count()
            if count == 0:
                broken += 1
                bad.append(f"MISSING   {css}")
            elif count > 1:
                ambiguous += 1
                bad.append(f"AMBIGUOUS {css} -> {count} matches")
        print(f"{mapped.url}\n  {len(selectors)} selectors, {len(bad)} problem(s)")
        for line in bad[:10]:
            print(f"    {line}")
    browser.close()

ok = total - broken - ambiguous
print(f"\n{ok}/{total} selectors resolve to exactly one element")
raise SystemExit(1 if broken or ambiguous else 0)
