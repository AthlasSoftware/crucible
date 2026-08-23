"""The script that runs *inside* the browser container.

Kept as a string (not a module) because it is executed by the container's
interpreter, not ours: it is written to /tmp on spawn and invoked once per tool
call with a JSON request as argv[1]. It attaches to the already-running
Chromium over CDP, performs one action, prints one JSON object on stdout, and
exits — so a crash in here can never take the browser down with it.

Contract: prints {"result": str} or {"error": str}; always exits 0 when it
managed to produce JSON, so the caller can tell "the page 404'd" (a result)
apart from "the driver broke" (a non-zero exit).
"""

from __future__ import annotations

CDP_PORT = 9222
# Trim page text before it reaches the model: a content-heavy page can be
# hundreds of KB, and the useful part is almost always near the top.
TEXT_CAP = 6000
LINK_CAP = 100

DRIVER_SOURCE = f'''\
import json
import sys

from playwright.sync_api import sync_playwright

TEXT_CAP = {TEXT_CAP}
LINK_CAP = {LINK_CAP}
CDP = "http://127.0.0.1:{CDP_PORT}"


def page_text(page):
    try:
        text = page.inner_text("body")
    except Exception:
        text = page.content()
    text = "\\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(text) > TEXT_CAP:
        text = text[:TEXT_CAP] + "\\n[... truncated, page continues ...]"
    return text


def described(page, note=""):
    head = f"title: {{page.title()}}\\nurl: {{page.url}}"
    if note:
        head += "\\n" + note
    return head + "\\n\\n" + page_text(page)


def do(page, action, args):
    if action == "goto":
        url = args.get("url", "")
        if not url:
            raise ValueError("no url given")
        if "://" not in url:
            url = "https://" + url
        response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        status = response.status if response else "?"
        return described(page, f"http status: {{status}}")

    if action == "read":
        return described(page)

    if action == "click":
        page.click(args["selector"], timeout=15000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        return described(page, f"clicked: {{args['selector']}}")

    if action == "type":
        page.fill(args["selector"], args.get("text", ""), timeout=15000)
        if args.get("enter"):
            page.press(args["selector"], "Enter")
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        return described(page, f"typed into: {{args['selector']}}")

    if action == "links":
        found = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => [ (e.innerText || '').trim().slice(0, 80), e.href ])",
        )
        seen, lines = set(), []
        for label, href in found:
            if href in seen:
                continue
            seen.add(href)
            lines.append(f"{{label or '(no text)'}} -> {{href}}")
            if len(lines) >= LINK_CAP:
                break
        return f"url: {{page.url}}\\n\\n" + ("\\n".join(lines) or "(no links)")

    raise ValueError(f"unknown action {{action!r}}")


def main():
    try:
        request = json.loads(sys.argv[1])
    except (IndexError, ValueError) as e:
        print(json.dumps({{"error": f"bad request: {{e}}"}}))
        return
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(CDP)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
            result = do(page, request.get("action", ""), request.get("args") or {{}})
        print(json.dumps({{"result": result}}))
    except Exception as e:
        # Page-level failures (bad selector, navigation timeout) are results the
        # model should see and react to, not driver crashes.
        print(json.dumps({{"error": f"{{type(e).__name__}}: {{e}}".strip()[:600]}}))


main()
'''
