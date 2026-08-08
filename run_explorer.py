"""CLI: python run_explorer.py <url> [--pages N] [--headed]"""

import argparse
from pathlib import Path

from agents.explorer import Explorer

parser = argparse.ArgumentParser()
parser.add_argument("url")
parser.add_argument("--pages", type=int, default=5)
parser.add_argument("--headed", action="store_true")
args = parser.parse_args()

print(f"Exploring {args.url} (max {args.pages} pages)...")
app_map = Explorer(max_pages=args.pages, headless=not args.headed).explore(args.url)

out = Path("output/app_map.json")
out.parent.mkdir(exist_ok=True)
out.write_text(app_map.model_dump_json(indent=2), encoding="utf-8")

print(f"\n{app_map.summary}\n")
for p in app_map.pages:
    print(f"[{p.title[:40]}] {p.purpose}")
    for a in p.actions:
        print(f"   - {a}")
print(f"\nSaved -> {out}")
