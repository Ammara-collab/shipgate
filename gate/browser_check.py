"""Browser checks for ShipGate (Playwright, in-sandbox).

Real path: Playwright boots the demo app page and screenshots the
claimed flows before/after a heal. Max 3 flows, controlled demo app
only (kill-switch ladder: browser -> screenshots-only fallback).

If Playwright (or its browser) is unavailable, this module returns a
clean 'skipped' result — it NEVER fabricates a screenshot. The
orchestrator records the skip in the dossier, and the verdict treats a
skipped browser check as non-blocking (a red browser check blocks).
"""

import os

MAX_FLOWS = 3


def playwright_available():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except Exception:
        return False


def run_browser_check(page_url, flows, out_dir):
    """flows: list of {name, url}. Screenshots up to MAX_FLOWS pages.
    Returns {status: 'green'|'red'|'skipped', screenshots: [...], note}."""
    if not playwright_available():
        return {
            "status": "skipped",
            "screenshots": [],
            "note": ("Playwright unavailable in this environment — "
                     "browser check skipped, no screenshot fabricated. "
                     "On the Vultr VM, install playwright + chromium."),
        }
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {"status": "skipped", "screenshots": [],
                "note": f"Playwright import failed: {exc}"}
    shots = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            for flow in flows[:MAX_FLOWS]:
                url = flow.get("url", page_url)
                page.goto(url)
                path = os.path.join(out_dir, f"{flow['name']}.png")
                page.screenshot(path=path)
                shots.append({"flow": flow["name"], "path": path})
            browser.close()
        return {"status": "green", "screenshots": shots,
                "note": f"{len(shots)} flow screenshot(s) captured"}
    except Exception as exc:
        return {"status": "red", "screenshots": shots,
                "note": f"browser check failed: {exc}"}
