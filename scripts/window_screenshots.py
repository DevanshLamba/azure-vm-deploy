"""Screenshots of the live site *with the browser address bar visible* (Windows only).

Playwright's page screenshots never include the browser UI, so this opens a normal (headed)
Chromium window and captures just that window with the Win32 PrintWindow API (never the
screen, so other apps can't leak into the image). Uses a fresh temporary profile (no cookies).

Usage: python scripts/window_screenshots.py --base-url http://20.205.122.152
"""
import argparse
import ctypes
import time
from ctypes import wintypes
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi
ctypes.windll.shcore.SetProcessDpiAwareness(2)  # physical pixels, no DPI virtualisation


def find_window(title_prefix: str) -> int:
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if (cls.value == "Chrome_WidgetWin_1" and buf.value.startswith(title_prefix)
                and buf.value.endswith("Google Chrome for Testing")):  # not VS Code, Edge, etc.
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    if len(found) != 1:
        raise RuntimeError(f"expected 1 window titled {title_prefix!r}, found {len(found)}")
    return found[0]


def grab_window(hwnd: int, path: Path):
    """Capture one window with PrintWindow: the window draws its *own* contents into our bitmap,
    so other apps on the screen can never end up in the image, even if they cover it."""
    gdi32 = ctypes.windll.gdi32
    time.sleep(1.0)
    win = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(win))
    frame = wintypes.RECT()  # visible frame, without the invisible resize border
    dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(frame), ctypes.sizeof(frame))
    w, h = win.right - win.left, win.bottom - win.top

    hdc_win = user32.GetWindowDC(hwnd)
    hdc = gdi32.CreateCompatibleDC(hdc_win)
    bmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    gdi32.SelectObject(hdc, bmp)
    ok = user32.PrintWindow(hwnd, hdc, 2)  # PW_RENDERFULLCONTENT: includes GPU-rendered content

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(hdc)
    user32.ReleaseDC(hwnd, hdc_win)
    if not ok:
        raise RuntimeError("PrintWindow failed")

    img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
    img = img.crop((frame.left - win.left, frame.top - win.top, frame.right - win.left, frame.bottom - win.top))
    lo, hi = img.convert("L").getextrema()
    if hi - lo < 20:  # a blank (all black/white) capture means rendering failed
        raise RuntimeError(f"capture of {path.name} looks blank")
    img.save(path)
    print("saved", path.relative_to(OUT.parent.parent), img.size)


def settle(page):
    """Top of the page, pointer parked in a corner (no hover effects in the shot)."""
    page.mouse.move(2, 2)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(400)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://20.205.122.152")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    with sync_playwright() as p:
        def open_window(width, height, x=60, y=40):
            b = p.chromium.launch(
                headless=False,
                ignore_default_args=["--enable-automation"],  # no "controlled by automated software" bar
                args=[f"--window-size={width},{height}", f"--window-position={x},{y}",
                      # keep painting even when other windows cover it (PrintWindow needs a fresh frame)
                      "--disable-backgrounding-occluded-windows",
                      "--disable-features=CalculateNativeWinOcclusion"],
            )
            ctx = b.new_context(no_viewport=True)
            ctx.add_init_script("try { localStorage.setItem('ct-theme', 'light'); localStorage.setItem('ct-filter', 'all'); } catch (e) {}")
            return b, ctx.new_page()

        # Desktop window
        b, page = open_window(1440, 940)
        page.goto(base + "/#tasks")
        page.wait_for_selector(".cell .card")
        page.wait_for_timeout(1500)
        settle(page); grab_window(find_window("CloudTasks"), OUT / "browser-desktop-tasks.png")

        page.click("#tab-status")
        page.wait_for_selector("#tiles[aria-busy='false']")
        page.wait_for_timeout(9000)  # let the sparklines fill
        settle(page); grab_window(find_window("CloudTasks"), OUT / "browser-desktop-status.png")

        page.goto(base + "/health")
        page.wait_for_timeout(1500)
        settle(page); grab_window(find_window(page.title() or base.split("//")[1]), OUT / "browser-health.png")
        b.close()

        # Narrow window: Chromium's minimum window width (~500 px) still triggers the 1-column mobile layout.
        b, page = open_window(500, 940, x=200)
        page.goto(base + "/#tasks")
        page.wait_for_selector(".cell .card")
        page.wait_for_timeout(1500)
        settle(page); grab_window(find_window("CloudTasks"), OUT / "browser-mobile-tasks.png")
        page.click("#tab-status")
        page.wait_for_selector("#tiles[aria-busy='false']")
        page.wait_for_timeout(6000)
        settle(page); grab_window(find_window("CloudTasks"), OUT / "browser-mobile-status.png")
        b.close()


if __name__ == "__main__":
    main()
