#!/usr/bin/env python3
"""Save a browser screenshot of a local HTML page."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
args.output.parent.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=1)
    page.goto(args.source.resolve().as_uri())
    page.screenshot(path=str(args.output), full_page=True)
    browser.close()
print(f"Saved {args.output}")
