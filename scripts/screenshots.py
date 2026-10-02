"""Take UI screenshots with Playwright: desktop + mobile, both views, both themes.

Usage:
  python scripts/screenshots.py --base-url http://localhost:8765 --reset
  python scripts/screenshots.py --base-url http://<public-ip> --prefix live-

--reset deletes every task first (only use it on your own local/dev instance), so the
empty state can be captured before the sample tasks are loaded.
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
VIEWPORTS = {
    "desktop": {"viewport": {"width": 1440, "height": 900}, "device_scale_factor": 1},
    "mobile": {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True},
}


def call(base, path, method="GET"):
    req = urllib.request.Request(base + path, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        body = r.read()
        return json.loads(body) if body else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8765")
    ap.add_argument("--reset", action="store_true", help="delete all tasks first (dev only)")
    ap.add_argument("--prefix", default="")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")
    OUT.mkdir(parents=True, exist_ok=True)
    errors = []

    def shot(page, name, full=True):
        path = OUT / f"{args.prefix}{name}.png"
        page.screenshot(path=str(path), full_page=full)
        print("saved", path.relative_to(OUT.parent.parent))

    with sync_playwright() as p:
        browser = p.chromium.launch()

        def new_page(kind, theme):
            ctx = browser.new_context(**VIEWPORTS[kind], color_scheme=theme)
            ctx.add_init_script(f"try {{ localStorage.setItem('ct-theme', '{theme}'); localStorage.setItem('ct-filter', 'all'); }} catch (e) {{}}")
            page = ctx.new_page()
            page.on("console", lambda m: m.type == "error" and errors.append(m.text))
            page.on("pageerror", lambda e: errors.append(str(e)))
            return page

        if args.reset:
            for t in call(base, "/api/tasks"):
                call(base, f"/api/tasks/{t['id']}", "DELETE")
            page = new_page("desktop", "light")
            page.goto(base + "/#tasks")
            page.wait_for_selector("#state-empty:not([hidden])")
            page.wait_for_timeout(700)
            shot(page, "desktop-light-empty", full=False)
            # Load the samples through the real UI button, then tick a few as done.
            page.click("#state-empty [data-action='samples']")
            page.wait_for_selector(".cell .card")
            page.wait_for_timeout(1200)
            for i in (2, 5, 8):
                page.locator(".cell .check").nth(i).click()
                page.wait_for_timeout(250)
            page.context.close()
        elif not call(base, "/api/tasks"):
            call(base, "/api/tasks/sample", "POST")

        for kind in ("desktop", "mobile"):
            for theme in ("light", "dark"):
                page = new_page(kind, theme)
                page.goto(base + "/#tasks")
                page.wait_for_selector(".cell .card")
                page.wait_for_timeout(1200)
                shot(page, f"{kind}-{theme}-tasks", full=kind == "desktop")

                page.click("#tab-status")
                page.wait_for_selector("#tiles[aria-busy='false']")
                page.wait_for_timeout(8000)  # let the sparklines collect a few points
                shot(page, f"{kind}-{theme}-status", full=kind == "desktop")
                page.context.close()

        # Composer modal (light, both sizes)
        for kind in ("desktop", "mobile"):
            page = new_page(kind, "light")
            page.goto(base + "/#tasks")
            page.wait_for_selector(".cell .card")
            page.keyboard.press("n")
            page.fill("#f-title", "Record the demo video")
            page.fill("#f-note", "Show the live VM status panel and the auto-shutdown setting.")
            page.click("label.seg-opt:has(input[value='high'])")
            page.click(".due-chip[data-due='1']")
            page.click(".swatch[data-color='lavender']")
            page.wait_for_timeout(700)
            shot(page, f"{kind}-light-composer", full=False)
            page.context.close()

        browser.close()

    if errors:
        print("\nBrowser console errors:", *errors, sep="\n  ")
        sys.exit(1)
    print("\nNo browser console errors.")


if __name__ == "__main__":
    t = time.time()
    main()
    print(f"done in {time.time() - t:.0f}s")
