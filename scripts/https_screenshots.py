"""Screenshots of the live HTTPS site, signed in as the read-only demo account.

The demo password is typed by you at a hidden prompt (getpass). It is only used to fill the
sign-in form in this run: never printed, saved or logged. The browser profile is temporary
and the script signs out at the end (which also deletes the session on the server).

Captures two kinds of images into docs/screenshots/:
  https-*.png          page screenshots (desktop + mobile, light + dark)
  https-window-*.png   real browser windows with the https:// address bar (Win32 PrintWindow)

Usage:  python scripts/https_screenshots.py
        python scripts/https_screenshots.py --pin-ip 20.205.122.152   (if local DNS can't resolve
        the domain yet: maps the name to the IP inside the browser; TLS is still verified)
"""
import argparse
import getpass
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import window_screenshots as ws  # noqa: E402  (find_window / grab_window / settle)

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
DESKTOP = {"viewport": {"width": 1440, "height": 900}}
MOBILE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="https://tasks.devanshlamba.in")
    ap.add_argument("--pin-ip", default="", help="map the site's hostname to this IP inside the browser")
    ap.add_argument("--username", default="demo")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")
    host = base.split("//")[1]

    if not sys.stdin.isatty():
        sys.exit("Run this in an interactive terminal: the password is read with a hidden prompt.")
    password = getpass.getpass(f"Password for the '{args.username}' account (hidden, never stored): ")
    if not password:
        sys.exit("No password entered.")

    launch_args = [f"--host-resolver-rules=MAP {host} {args.pin_ip}"] if args.pin_ip else []
    errors = []

    def sign_in(page):
        page.goto(base + "/login")
        page.wait_for_selector("#login-form")
        page.fill("#username", args.username)
        page.fill("#password", password)
        page.click("#login-submit")
        page.wait_for_selector("#user-chip:not([hidden])", timeout=20000)
        page.wait_for_selector(".cell .card")
        page.wait_for_timeout(1500)

    def sign_out(page):
        try:
            page.click("#logout")
            page.wait_for_url("**/login", timeout=10000)
        except Exception:
            pass

    def shot(page, name, full=False):
        path = OUT / f"{name}.png"
        page.screenshot(path=str(path), full_page=full)
        print("saved", path.relative_to(OUT.parent.parent))

    with sync_playwright() as p:
        # ---- page screenshots (headless)
        browser = p.chromium.launch(args=launch_args)
        for kind, opts in (("desktop", DESKTOP), ("mobile", MOBILE)):
            for theme in ("light", "dark"):
                ctx = browser.new_context(**opts)
                ctx.add_init_script(f"try {{ localStorage.setItem('ct-theme', '{theme}'); localStorage.setItem('ct-filter', 'all'); }} catch (e) {{}}")
                page = ctx.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(base + "/login")
                page.wait_for_selector("#login-form")
                page.wait_for_timeout(800)
                shot(page, f"https-login-{kind}-{theme}")
                sign_in(page)
                shot(page, f"https-demo-tasks-{kind}-{theme}", full=(kind == "desktop"))
                if theme == "light":
                    page.locator(".cell .check").first.dispatch_event("click")  # read-only message
                    page.wait_for_selector(".toast")
                    page.wait_for_timeout(500)
                    shot(page, f"https-demo-readonly-{kind}")
                page.click("#tab-status")
                page.wait_for_selector("#tiles[aria-busy='false']")
                page.wait_for_timeout(7000)
                shot(page, f"https-demo-status-{kind}-{theme}", full=(kind == "desktop"))
                sign_out(page)
                ctx.close()
        browser.close()

        # ---- real browser windows with the address bar (headed, captured with PrintWindow)
        for kind, size in (("desktop", (1440, 940)), ("mobile", (500, 940))):
            b = p.chromium.launch(
                headless=False, ignore_default_args=["--enable-automation"],
                args=launch_args + [f"--window-size={size[0]},{size[1]}", "--window-position=60,40",
                                    "--disable-backgrounding-occluded-windows",
                                    "--disable-features=CalculateNativeWinOcclusion"])
            ctx = b.new_context(no_viewport=True)
            ctx.add_init_script("try { localStorage.setItem('ct-theme', 'light'); localStorage.setItem('ct-filter', 'all'); } catch (e) {}")
            page = ctx.new_page()
            page.goto(base + "/login")
            page.wait_for_selector("#login-form")
            page.wait_for_timeout(800)
            if kind == "desktop":
                ws.settle(page)
                ws.grab_window(ws.find_window("Sign in"), OUT / "https-window-login.png")
            sign_in(page)
            ws.settle(page)
            ws.grab_window(ws.find_window("CloudTasks"), OUT / f"https-window-demo-tasks-{kind}.png")
            if kind == "desktop":
                page.click("#tab-status")
                page.wait_for_selector("#tiles[aria-busy='false']")
                page.wait_for_timeout(3000)
                ws.settle(page)
                ws.grab_window(ws.find_window("CloudTasks"), OUT / "https-window-demo-status.png")
            sign_out(page)
            b.close()

    password = None  # drop the reference as soon as we're done
    print("\nBrowser errors:", errors or "none")
    print("Done. All sessions were signed out. Tell Claude: screenshots done")


if __name__ == "__main__":
    main()
