#!/usr/bin/env python3
"""Render the terminal captures in results/demo/*.svg (written by tools/demo_sim.py and tools/vmcu/run_vmcu.py) to
PNG with headless Chromium, for the slides and the paper. Usage: python tools/svg2png.py [--scale 2]"""
from __future__ import annotations

import argparse
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scale", type=float, default=2.0)
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(device_scale_factor=a.scale)
        for svg in sorted((REPO / "results" / "demo").glob("*.svg")):
            pg.goto(svg.as_uri())
            el = pg.query_selector("svg")
            el.screenshot(path=str(svg.with_suffix(".png")))
            print(f"wrote {svg.with_suffix('.png').relative_to(REPO)}")
        b.close()


if __name__ == "__main__":
    main()
