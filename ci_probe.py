#!/usr/bin/env python3
"""TEMPORARY - probe boards that block this repo's dev sandbox, from the
Actions runner's network. Prints what each host returns; changes no state.
Removed once the results are read."""
import re

import requests

import jobscan as J

H = {**J.HEADERS, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
API = re.compile(r"https?://[a-zA-Z0-9.-]+/[a-zA-Z0-9_/.-]*(?:api|search|jobs?|career|widgets|ajax)"
                 r"[a-zA-Z0-9_/.?=&%-]*")


def text_of(page):
    return re.sub(r"\s+", " ", J.strip_html(re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S)))


print("=== plain requests ===")
for u in ["https://careers.cognizant.com/global-en/jobs/",
          "https://careers.cognizant.com/global-en/jobs/?location=India",
          "https://careers.cognizant.com/global-en/search-results",
          "https://www.mastek.com/careers/",
          "https://careers.happiestminds.com/"]:
    try:
        r = requests.get(u, headers=H, timeout=30)
        t = r.text
        print(f"\n## PLAIN {r.status_code} {len(t)} {r.url[:100]}")
        print("   title:", re.findall(r"<title>([^<]*)", t)[:1])
        print("   text:", text_of(t)[:700])
        print("   scripts:", re.findall(r'<script[^>]+src="([^"]+)"', t)[:12])
        print("   apis:", sorted(set(API.findall(t)))[:20])
        print("   job links:", sorted(set(re.findall(r'href="([^"]*(?:/job/|/jobs/|JobDetail|jobid|job-id)[^"]*)"', t, re.I)))[:8])
    except Exception as e:
        print("PLAIN ERR", u, type(e).__name__, str(e)[:100])

print("\n=== real headless chromium ===")
from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    for url in ["https://www.globallogic.com/career-search-page/?country=india",
                "https://www.kpit.com/job-listing/",
                "https://www.mastek.com/careers/",
                "https://careers.happiestminds.com/"]:
        ctx = b.new_context(user_agent=J.HEADERS["User-Agent"], locale="en-US")
        pg = ctx.new_page()
        calls = []

        def on_resp(r, c=calls):
            if r.request.resource_type in ("xhr", "fetch"):
                snippet = ""
                try:
                    if "json" in (r.headers.get("content-type") or ""):
                        snippet = r.text()[:200].replace("\n", " ")
                except Exception:
                    pass
                c.append((r.status, r.request.method, r.url[:140], snippet))
        pg.on("response", on_resp)
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print("BROWSER goto err", url, str(e)[:90])
        t = ""
        for _ in range(10):
            pg.wait_for_timeout(3000)
            try:
                t = pg.content()
            except Exception:
                continue
        print(f"\n## BROWSER {url[:70]} -> {pg.url[:80]} title={pg.title()[:60]!r} len={len(t)}")
        print("   text:", text_of(t)[:600])
        print("   job links:", sorted(set(re.findall(r'href="([^"]*(?:/job/|/jobs/|job-detail|jobid|job-id)[^"]*)"', t, re.I)))[:8])
        for c in calls[:25]:
            print("   xhr", c)
        ctx.close()
    b.close()
