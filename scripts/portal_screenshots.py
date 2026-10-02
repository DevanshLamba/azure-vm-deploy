"""Azure portal screenshots for the report, with sensitive values masked BEFORE capture.

Attaches to a Chromium window the user opened and signed in to themselves (persistent profile
outside the repo, remote debugging on 127.0.0.1 only). This script never touches the sign-in
pages, never reads cookies or credentials, and stops if a sign-in page appears.

Before every capture it replaces, in every frame (including the portal's iframes and shadow DOM):
GUIDs (subscription / tenant / object IDs), e-mail addresses, the user's home IP address and long
numeric IDs. It then re-reads the page text and refuses to save if anything sensitive remains.
Subscription/tenant IDs are fetched with az into memory only (to build URLs and to verify the
masking) and are never printed or written to disk.

Usage: python scripts/portal_screenshots.py a b c ...   (letters from SHOTS, default: all)
"""
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
RG, VM, NSG, DISK, PIP = "rg-cloudtasks", "vm-cloudtasks", "nsg-cloudtasks", "vm-cloudtasks-osdisk", "pip-cloudtasks"
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
HEX32 = re.compile(r"\b[0-9a-fA-F]{32}\b")


def az(*args) -> str:
    r = subprocess.run(["az", *args, "-o", "tsv"], capture_output=True, text=True, shell=True)
    if r.returncode != 0:
        raise RuntimeError(f"az {args[0]} {args[1]} failed")  # no stderr: it may contain IDs
    return r.stdout.strip()


def safe(msg) -> str:
    return GUID.sub("<guid>", str(msg))


MASK_JS = r"""
([home, extra]) => {
  const pats = [
    /[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/g, // GUIDs
    /[\w.+-]+@[\w-]+(\.[\w-]+)+/g,                                                  // e-mail
    /\b\d{9,}\b/g,                                                                   // long numeric IDs
    /\b[0-9a-fA-F]{32}\b/g,                                                          // undashed IDs (session, correlation)
  ];
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  for (const v of [home, ...extra]) if (v) pats.push(new RegExp(esc(v), "gi"));
  const mask = s => { let t = s; for (const p of pats) t = t.replace(p, "████████"); return t; };
  const walk = root => {
    const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let n; while ((n = tw.nextNode())) { const m = mask(n.nodeValue); if (m !== n.nodeValue) n.nodeValue = m; }
    for (const el of root.querySelectorAll("*")) {
      if (el.shadowRoot) walk(el.shadowRoot);
      for (const a of ["title", "aria-label", "placeholder", "value"]) {
        const v = el.getAttribute && el.getAttribute(a);
        if (v) { const m = mask(v); if (m !== v) el.setAttribute(a, m); }
      }
      if ((el.tagName === "INPUT" || el.tagName === "TEXTAREA") && el.value) { const m = mask(el.value); if (m !== el.value) el.value = m; }
    }
  };
  walk(document);
  if (!window.__ctMaskObserver) {   // keep masking content that renders later
    window.__ctMaskObserver = new MutationObserver(() => walk(document));
    window.__ctMaskObserver.observe(document, { subtree: true, childList: true, characterData: true });
  }
  return true;
}
"""

TEXT_JS = r"""
() => {
  const parts = [];
  const walk = root => {
    const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let n; while ((n = tw.nextNode())) parts.push(n.nodeValue);
    for (const el of root.querySelectorAll("*")) {
      if (el.shadowRoot) walk(el.shadowRoot);
      for (const a of ["title", "aria-label", "placeholder", "value"]) { const v = el.getAttribute && el.getAttribute(a); if (v) parts.push(v); }
      if ((el.tagName === "INPUT" || el.tagName === "TEXTAREA") && el.value) parts.push(el.value);
    }
  };
  walk(document);
  return parts.join("\n");
}
"""


def main():
    want = sys.argv[1:] or list("abcdefghi")
    sub = az("account", "show", "--query", "id")
    tenant = az("account", "show", "--query", "tenantId")
    home_ip = urllib.request.urlopen("https://api.ipify.org", timeout=10).read().decode().strip()
    base = f"/subscriptions/{sub}/resourceGroups/{RG}/providers"
    rid = {
        "rg": f"/subscriptions/{sub}/resourceGroups/{RG}",
        "vm": f"{base}/Microsoft.Compute/virtualMachines/{VM}",
        "nsg": f"{base}/Microsoft.Network/networkSecurityGroups/{NSG}",
        "disk": f"{base}/Microsoft.Compute/disks/{DISK}",
        "pip": f"{base}/Microsoft.Network/publicIPAddresses/{PIP}",
    }
    # "#@<tenant>" makes the portal switch to the directory that owns the subscription.
    portal = f"https://portal.azure.com/#@{tenant}/resource"
    shots = {
        "a": ("portal-a-resource-group", f"{portal}{rid['rg']}/overview", None),
        "b": ("portal-b-vm-overview", f"{portal}{rid['vm']}/overview", None),
        "c": ("portal-c-nsg-inbound-rules", f"{portal}{rid['nsg']}/inboundSecurityRules", None),
        "d": ("portal-d-auto-shutdown", f"{portal}{rid['vm']}/autoShutdown", None),
        "e": ("portal-e-os-disk", f"{portal}{rid['disk']}/overview", None),
        "f": ("portal-f-public-ip", f"{portal}{rid['pip']}/overview", None),
        "g": ("portal-g-vm-cpu-metrics", f"{portal}{rid['vm']}/overview", "monitoring"),
        "h": ("portal-h-policy-allowed-regions",
              f"https://portal.azure.com/#@{tenant}/view/Microsoft_Azure_Policy/PolicyMenuBlade/~/Assignments", "policy"),
        "i": ("portal-i-cost-analysis", f"{portal}{rid['rg']}/costanalysis", None),
        "j": ("portal-j-vm-stopped", f"{portal}{rid['vm']}/overview", None),
    }

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9333")
        pages = [pg for ctx in browser.contexts for pg in ctx.pages if "portal.azure.com" in pg.url]
        if not pages:
            raise SystemExit("STOP: no portal.azure.com tab found in the portal window.")
        page = pages[0]
        page.bring_to_front()

        def guard():
            host = page.url.split("/")[2] if "//" in page.url else ""
            if "login." in host or "sign in" in page.title().lower():
                raise SystemExit("STOP: a sign-in page appeared. Please sign in yourself; nothing was captured.")

        def mask_all():
            for fr in page.frames:
                try:
                    fr.evaluate(MASK_JS, [home_ip, [sub, tenant]])
                except Exception:
                    pass  # detached / about:blank frames

        def leftovers():
            bad = set()
            for fr in page.frames:
                try:
                    t = fr.evaluate(TEXT_JS)
                except Exception:
                    continue
                low = t.lower()
                if sub.lower() in low or tenant.lower() in low:
                    bad.add("subscription/tenant ID")
                if GUID.search(t) or HEX32.search(t):
                    bad.add("GUID / hex ID")
                if "You don't have access" in t or "No access" in t:
                    bad.add("access-error page (wrong directory?)")
                if home_ip in t:
                    bad.add("home IP")
                if re.search(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", t):
                    bad.add("e-mail")
            return bad

        for key in want:
            name, url, extra = shots[key]
            page.goto(url)
            page.wait_for_timeout(14000)  # the portal loads blades lazily
            guard()
            page.keyboard.press("Escape")  # close any welcome / tour popup
            if extra == "monitoring":
                try:
                    page.get_by_role("tab", name=re.compile("^Monitoring")).first.click(timeout=8000)
                    page.wait_for_timeout(9000)
                except Exception as e:
                    print(f"({key}) could not open the Monitoring tab: {safe(e)[:120]}")
            if extra == "policy":
                try:
                    page.get_by_text("Allowed resource deployment regions").first.click(timeout=8000)
                    page.wait_for_timeout(9000)
                except Exception as e:
                    print(f"({key}) could not open the assignment: {safe(e)[:120]}")
            guard()
            page.mouse.move(5, 300)
            mask_all()
            page.wait_for_timeout(1500)
            mask_all()
            bad = leftovers()
            if bad:
                print(f"({key}) NOT SAVED: still visible after masking: {', '.join(sorted(bad))}")
                continue
            path = OUT / f"{name}.png"
            page.screenshot(path=str(path))
            print(f"({key}) saved {path.relative_to(OUT.parent.parent)}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # never print raw errors: URLs in them contain the subscription ID
        print("ERROR:", safe(e)[:400])
        sys.exit(1)
