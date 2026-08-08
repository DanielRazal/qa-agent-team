"""Explorer agent: crawls an app and produces a structured AppMap."""

import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

from core.llm import LLM
from core.models import AppMap, Element, Form, Page

_JS = (Path(__file__).parent / "extract.js").read_text(encoding="utf-8")

class BlockedError(RuntimeError):
    """The crawler was served a bot check instead of the application."""


# A bot wall maps perfectly well - and produces a suite that proves nothing.
_CHALLENGE = re.compile(
    r"just a moment|attention required|verify you are human|checking your browser"
    r"|security check|access denied|are you a robot",
    re.I,
)
_BLOCKED_STATUS = {401, 403, 407, 429, 503}


def _blocked_reason(status: int, data: dict) -> str | None:
    if status in _BLOCKED_STATUS:
        return f"HTTP {status}"
    if _CHALLENGE.search(data.get("title", "")):
        return f"challenge page titled {data['title']!r}"
    if any(_CHALLENGE.search(h) for h in data.get("headings", [])):
        return "challenge page heading"
    thin = len(data.get("elements", [])) <= 5
    if thin and any("cloudflare.com" in (e.get("href") or "") for e in data.get("elements", [])):
        return "Cloudflare interstitial"
    return None


SYSTEM = """You are a senior QA engineer mapping an unfamiliar web app.
You get raw element data scraped from real pages. Describe what each page is FOR
and which concrete user actions it exposes. Be specific, never generic."""


class Explorer:
    def __init__(
        self,
        max_pages: int = 5,
        headless: bool = True,
        enrich: bool = True,
        llm: LLM | None = None,
    ):
        self.max_pages = max_pages
        self.headless = headless
        self.enrich = enrich
        self.llm = llm if llm or not enrich else LLM()

    def explore(self, base_url: str) -> AppMap:
        raw = self._crawl(base_url)
        pages = [self._to_page(r) for r in raw]
        app_map = AppMap(base_url=base_url, pages=pages)
        if self.enrich:
            self._enrich(app_map, raw)
        return app_map

    @staticmethod
    def _key(url: str) -> str:
        """Same page under different spellings must count once."""
        return url.split("#")[0].split("?")[0].rstrip("/").lower()

    def _crawl(self, base_url: str) -> list[dict]:
        seen, queue, results = set(), [base_url], []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            page = browser.new_page()
            while queue and len(results) < self.max_pages:
                url = queue.pop(0)
                if self._key(url) in seen:
                    continue
                seen.add(self._key(url))
                try:
                    response = page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    self._settle(page)
                    data = page.evaluate(_JS)
                except Exception as e:
                    print(f"  ! skipped {url}: {type(e).__name__}")
                    continue

                blocked = _blocked_reason(response.status if response else 0, data)
                if blocked and not results:
                    raise BlockedError(
                        f"{url} served a bot check, not the app ({blocked}). "
                        "Anything mapped from here would be a test of the bot wall."
                    )
                if blocked:
                    print(f"  ! skipped {url}: {blocked}")
                    continue

                seen.add(self._key(data["url"]))  # redirects
                results.append(data)
                print(f"  + {data['title'][:50]} ({len(data['elements'])} elements)")
                queue += [l for l in data["links"] if self._key(l) not in seen]
            browser.close()
        return results

    @staticmethod
    def _settle(page) -> None:
        """Wait out client-side rendering before scraping."""
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(500)

    def _to_page(self, raw: dict) -> Page:
        forms = [
            Form(
                name=f["name"],
                css=f["css"],
                fields=[Element(**x) for x in f["fields"]],
                submit=Element(**f["submit"]) if f["submit"] else None,
            )
            for f in raw["forms"]
        ]
        return Page(
            url=raw["url"],
            title=raw["title"],
            headings=raw["headings"],
            elements=[Element(**e) for e in raw["elements"]],
            forms=forms,
        )

    def _enrich(self, app_map: AppMap, raw: list[dict]) -> None:
        """Ask the LLM what each page is for and what a user can do there."""
        digest = [
            {
                "url": r["url"],
                "title": r["title"],
                "headings": r["headings"],
                "forms": [{"name": f["name"], "fields": [x["label"] for x in f["fields"]]} for f in r["forms"]],
                "controls": [f"{e['kind']}: {e['label']}" for e in r["elements"] if e["label"]][:40],
            }
            for r in raw
        ]
        prompt = (
            "Here is scraped data from a web app:\n"
            f"{json.dumps(digest, ensure_ascii=False)[:30000]}\n\n"
            'Return JSON: {"summary": "<what this app does, 1-2 sentences>", '
            '"pages": [{"url": "<exact url>", "purpose": "<one line>", '
            '"actions": ["<user action>", ...]}]}'
        )
        try:
            result = self.llm.ask_json(prompt, system=SYSTEM)
        except Exception as e:
            print(f"  ! enrichment failed: {e}")
            return

        app_map.summary = result.get("summary", "")
        by_url = {p["url"]: p for p in result.get("pages", [])}
        for page in app_map.pages:
            info = by_url.get(page.url, {})
            page.purpose = info.get("purpose", "")
            page.actions = info.get("actions", [])
