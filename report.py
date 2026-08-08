"""Render the run as a standalone HTML report.

Usage: python report.py [--out output/report.html] [--fragment]
--fragment emits style+body only, for embedding somewhere else.
"""

import argparse
import json
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from html import escape
from pathlib import Path

OUT = Path("output")

CATEGORY_LABEL = {
    "product_bug": "product bug",
    "selector_drift": "selector drift",
    "test_defect": "test defect",
    "flaky": "flaky",
    "environment": "environment",
}

STYLE = """
:root {
  --bg: #f6f7f9; --surface: #ffffff; --line: #e2e5ea;
  --text: #14181f; --muted: #5d6673;
  --accent: #3b5bdb; --ok: #1f8a4c; --warn: #b26a00; --bad: #c0392b; --info: #6741d9;
  --shadow: 0 1px 2px rgba(16,20,28,.06), 0 4px 16px rgba(16,20,28,.05);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0e1116; --surface: #161b22; --line: #262c36;
    --text: #e6e9ee; --muted: #9aa4b2;
    --accent: #7d97f4; --ok: #4ec97e; --warn: #e0a458; --bad: #f0796b; --info: #b197fc;
    --shadow: 0 1px 2px rgba(0,0,0,.4), 0 4px 16px rgba(0,0,0,.3);
  }
}
:root[data-theme="dark"] {
  --bg: #0e1116; --surface: #161b22; --line: #262c36;
  --text: #e6e9ee; --muted: #9aa4b2;
  --accent: #7d97f4; --ok: #4ec97e; --warn: #e0a458; --bad: #f0796b; --info: #b197fc;
  --shadow: 0 1px 2px rgba(0,0,0,.4), 0 4px 16px rgba(0,0,0,.3);
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
.wrap { max-width: 1040px; margin: 0 auto; padding: 40px 20px 72px; }
header { margin-bottom: 28px; }
h1 { font-size: 26px; margin: 0 0 6px; letter-spacing: -.01em; }
.sub { color: var(--muted); font-size: 14px; }
.sub a { color: var(--accent); }
h2 { font-size: 17px; margin: 36px 0 12px; letter-spacing: -.005em; }
.tiles { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
.tile {
  background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
  padding: 14px 16px; box-shadow: var(--shadow);
}
.tile .n { font-size: 26px; font-weight: 600; letter-spacing: -.02em; }
.tile .k { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }
.card {
  background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
  box-shadow: var(--shadow); overflow: hidden;
}
.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 10px 14px; border-bottom: 1px solid var(--line); }
th { color: var(--muted); font-weight: 500; font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }
tr:last-child td { border-bottom: 0; }
td.id { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; color: var(--muted); white-space: nowrap; }
.badge {
  display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px;
  border: 1px solid currentColor; white-space: nowrap;
}
.positive, .ok { color: var(--ok); }
.negative, .bad { color: var(--bad); }
.edge, .warn { color: var(--warn); }
.high { color: var(--bad); } .medium { color: var(--warn); } .low { color: var(--muted); }
.info { color: var(--info); }
details { border-top: 1px solid var(--line); }
details:first-of-type { border-top: 0; }
summary { padding: 12px 16px; cursor: pointer; font-size: 14px; }
summary::marker { color: var(--muted); }
.body { padding: 0 16px 14px; }
.steps { margin: 0; padding-left: 18px; color: var(--muted); font-size: 13px; }
.steps code {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px; color: var(--text);
}
.kv { margin: 6px 0; font-size: 14px; }
.kv b { color: var(--muted); font-weight: 500; }
.empty { color: var(--muted); padding: 16px; font-size: 14px; }
footer { margin-top: 44px; color: var(--muted); font-size: 12px; }
"""


def load(name: str):
    path = OUT / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def suite_counts() -> tuple[int, int] | None:
    path = OUT / "results.xml"
    if not path.exists():
        return None
    suite = ET.parse(path).getroot().find("testsuite")
    if suite is None:
        return None
    total = int(suite.get("tests", 0))
    bad = int(suite.get("failures", 0)) + int(suite.get("errors", 0))
    return total - bad, total


def tile(number, label) -> str:
    return f'<div class="tile"><div class="n">{escape(str(number))}</div><div class="k">{escape(label)}</div></div>'


def step_line(step: dict) -> str:
    bits = [f"<b>{escape(step['action'])}</b>"]
    if step.get("target"):
        bits.append(f"<code>{escape(step['target'])}</code>")
    if step.get("value"):
        bits.append(f"&rarr; {escape(step['value'])}")
    return f"<li>{' '.join(bits)}</li>"


def build_body(app_map, plan, triage) -> str:
    stamp = datetime.now().strftime("%d %b %Y, %H:%M")
    out = ['<div class="wrap"><header>']
    out.append("<h1>QA Agent Team report</h1>")

    if not app_map:
        out.append('<p class="sub">No run found. Run <code>python main.py &lt;url&gt;</code> first.</p>')
        return "".join(out) + "</header></div>"

    base = app_map.get("base_url", "")
    out.append(
        f'<p class="sub">Target <a href="{escape(base)}">{escape(base)}</a> '
        f"&middot; generated {escape(stamp)}</p></header>"
    )
    if app_map.get("summary"):
        out.append(f'<p class="sub">{escape(app_map["summary"])}</p>')

    pages = app_map.get("pages", [])
    elements = sum(len(p.get("elements", [])) for p in pages)
    cases = (plan or {}).get("cases", [])
    kinds = Counter(c["kind"] for c in cases)

    tiles = [tile(len(pages), "pages mapped"), tile(elements, "elements found"), tile(len(cases), "test cases")]
    suite = suite_counts()
    if suite:
        passed, total = suite
        tiles.append(tile(f"{passed}/{total}", "tests passed"))
    out.append(f'<div class="tiles">{"".join(tiles)}</div>')

    # --- test cases ---
    out.append("<h2>Designed test cases</h2>")
    if not cases:
        out.append('<div class="card"><p class="empty">No test cases were produced.</p></div>')
    else:
        breakdown = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))
        out.append(f'<p class="sub" style="margin:0 0 10px">{escape(breakdown)}</p><div class="card">')
        for case in cases:
            out.append(
                f"<details><summary>"
                f'<span class="badge {case["priority"]}">{escape(case["priority"])}</span> '
                f'<span class="badge {case["kind"]}">{escape(case["kind"])}</span> '
                f'<b>{escape(case["id"])}</b> {escape(case["title"])}'
                f'</summary><div class="body">'
            )
            if case.get("expected"):
                out.append(f'<p class="kv"><b>Expected:</b> {escape(case["expected"])}</p>')
            out.append('<ol class="steps">' + "".join(step_line(s) for s in case.get("steps", [])) + "</ol>")
            out.append("</div></details>")
        out.append("</div>")

    # --- triage ---
    if triage and triage.get("verdicts"):
        out.append("<h2>Triage</h2><div class=\"card\">")
        for v in triage["verdicts"]:
            tone = {"product_bug": "bad", "selector_drift": "warn", "flaky": "info"}.get(v["category"], "muted")
            label = CATEGORY_LABEL.get(v["category"], v["category"])
            out.append(
                f"<details><summary>"
                f'<span class="badge {tone}">{escape(label)}</span> '
                f'<span class="badge {v["confidence"]}">{escape(v["confidence"])}</span> '
                f'{escape(v["test"])}</summary><div class="body">'
                f'<p class="kv"><b>Why:</b> {escape(v["root_cause"])}</p>'
                f'<p class="kv"><b>Evidence:</b> {escape(v["evidence"])}</p>'
                f'<p class="kv"><b>Action:</b> {escape(v["action"])}</p>'
                f"</div></details>"
            )
        out.append("</div>")
    elif suite:
        out.append('<h2>Triage</h2><div class="card"><p class="empty">Every test passed. Nothing to triage.</p></div>')

    # --- map ---
    out.append('<h2>Application map</h2><div class="card scroll"><table><thead><tr>'
               "<th>Page</th><th>Purpose</th><th>Elements</th><th>Forms</th>"
               "</tr></thead><tbody>")
    for p in pages:
        out.append(
            f'<tr><td><a href="{escape(p["url"])}">{escape(p.get("title") or p["url"])}</a></td>'
            f'<td>{escape(p.get("purpose", ""))}</td>'
            f'<td>{len(p.get("elements", []))}</td><td>{len(p.get("forms", []))}</td></tr>'
        )
    out.append("</tbody></table></div>")
    out.append('<footer>Generated by qa-agent-team &middot; Explorer &rarr; Designer &rarr; Automator &rarr; Triage</footer>')
    return "".join(out) + "</div>"


parser = argparse.ArgumentParser()
parser.add_argument("--out", default="output/report.html")
parser.add_argument("--fragment", action="store_true", help="style + body only")
args = parser.parse_args()

body = build_body(load("app_map.json"), load("test_plan.json"), load("triage_report.json"))
style = f"<style>{STYLE}</style>"

if args.fragment:
    page = style + body
else:
    page = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>QA Agent Team report</title>"
        f"{style}</head><body>{body}</body></html>"
    )

path = Path(args.out)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(page, encoding="utf-8")
print(f"Saved -> {path}")
