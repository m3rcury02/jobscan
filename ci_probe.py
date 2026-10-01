#!/usr/bin/env python3
"""TEMPORARY - probe boards that block this repo's dev sandbox, from the
Actions runner's network. Prints what each host returns; changes no state.
Removed once the results are read."""
import re

import requests

import jobscan as J

H = {**J.HEADERS, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


def text_of(page):
    return re.sub(r"\s+", " ", J.strip_html(re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S)))


print("=== cognizant, plain requests ===")
s = requests.Session()
s.headers.update(H)
for u in ["https://careers.cognizant.com/global-en/jobs/",
          "https://careers.cognizant.com/india-en/jobs/",
          "https://careers.cognizant.com/global-en/jobs/?keyword=&location=India&page=2"]:
    try:
        r = s.get(u, timeout=30)
        t = r.text
        print(f"\n## {r.status_code} {len(t)} {r.url[:100]} title={re.findall(r'<title>([^<]*)', t)[:1]}")
        hrefs = sorted(set(re.findall(r'href="([^"#]+)"', t)))
        print("   job-ish hrefs:", [h for h in hrefs if re.search(r"job|search|page=", h, re.I)][:25])
        print("   forms:", re.findall(r"<form[^>]*>", t)[:4])
        print("   data attrs:", sorted(set(re.findall(r"data-[a-z-]*(?:job|total|count|page)[a-z-]*=\"[^\"]{0,40}\"", t)))[:10])
        i = t.lower().find("results")
        print("   near 'results':", text_of(t[max(0, i - 400):i + 1200])[:700] if i > 0 else "-")
    except Exception as e:
        print("ERR", u, type(e).__name__, str(e)[:100])
try:
    app = s.get("https://careers.cognizant.com/phb/app.js.v0b621e2581d05d0b89f0e8acac710efbe2d0fa7d", timeout=30).text
    print("\n## app.js", len(app))
    print("   paths:", sorted(set(re.findall(r"[\"'`](/[a-zA-Z0-9_/-]*(?:api|search|jobs?|ajax|results)[a-zA-Z0-9_/?=&.-]*)[\"'`]", app)))[:30])
    print("   fetch:", re.findall(r"fetch\([^)]{0,140}\)", app)[:8])
except Exception as e:
    print("app.js ERR", e)

print("\n=== kpit, real browser ===")
from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(user_agent=J.HEADERS["User-Agent"], locale="en-US")
    pg = ctx.new_page()
    calls = []
    pg.on("response", lambda r: calls.append((r.status, r.request.method, r.url[:150]))
          if r.request.resource_type in ("xhr", "fetch", "document") else None)
    pg.goto("https://www.kpit.com/job-listing/", wait_until="domcontentloaded", timeout=60000)
    pg.wait_for_timeout(20000)
    t = pg.content()
    anchors = pg.eval_on_selector_all("a", "els => els.map(e => [e.innerText.trim().slice(0,60), e.href])")
    print("anchors with job/apply/career:", [a for a in anchors if re.search(r"job|apply|career|opening", (a[0] or "") + (a[1] or ""), re.I)][:30])
    print("iframes:", pg.eval_on_selector_all("iframe", "els => els.map(e => e.src)"))
    i = t.find("Experience")
    print("near Experience:", text_of(t[max(0, i - 600):i + 900])[:800] if i > 0 else "-")
    for c in calls[:30]:
        print("   net", c)
    b.close()
