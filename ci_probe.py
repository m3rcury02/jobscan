#!/usr/bin/env python3
"""TEMPORARY - probe boards that block this repo's dev sandbox, from the
Actions runner's network. Prints what each host returns; changes no state.
Removed once the results are read."""
import json
import re

import requests

import jobscan as J

H = {**J.HEADERS, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
SIGS = r"(myworkdayjobs[^\"' ]{0,50}|successfactors|jobs2web|phenom[a-z]*|ripplehire|darwinbox|" \
       r"oraclecloud[^\"' ]{0,40}|icims|taleo|avature|greenhouse|lever\.co|smartrecruiters|" \
       r"eightfold|zwayam|refNum\":\"[A-Z0-9]+|Just a moment|challenge-platform)"


def plain(url, method="GET", **kw):
    try:
        r = requests.request(method, url, headers=H, timeout=30, **kw)
        sig = sorted(set(re.findall(SIGS, r.text)))[:10]
        print(f"PLAIN {r.status_code} {len(r.text):7} {url[:90]}\n      {sig}")
        return r
    except Exception as e:
        print(f"PLAIN ERR {type(e).__name__}: {str(e)[:120]} {url[:90]}")


print("=== plain requests ===")
for u in ["https://careers.cognizant.com/global-en/jobs/",
          "https://careers.epam.in/", "https://careers.epam.in/vacancies/job-listings",
          "https://www.globallogic.com/career-search-page/?country=india",
          "https://kpitcareers.kpit.com/", "https://jobs.brillio.com/",
          "https://careers.happiestminds.com/", "https://www.mastek.com/careers/",
          "https://careers.publicisgroupe.com/jobs?location=India",
          "https://www.virtusa.com/careers"]:
    plain(u)

print("=== workday shards unreachable from the sandbox ===")
for ten in ["cognizant", "epam", "globallogic", "kpit", "brillio", "happiestminds",
            "mastek", "publicissapient", "publicisgroupe", "virtusa", "techmahindra"]:
    for shard in ["wd1", "wd2", "wd3", "wd5", "wd101", "wd103", "wd12"]:
        try:
            r = requests.post(f"https://{ten}.{shard}.myworkdayjobs.com/wday/cxs/{ten}/BOGUS/jobs",
                              json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
                              headers={**J.HEADERS, "Content-Type": "application/json"}, timeout=15)
            if r.status_code != 422:
                print(f"WD {ten}.{shard} -> {r.status_code}")
        except Exception as e:
            print(f"WD {ten}.{shard} ERR {type(e).__name__}")

print("=== real headless chromium ===")
from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    for url in ["https://careers.cognizant.com/global-en/jobs/?location=India",
                "https://careers.epam.in/vacancies/job-listings",
                "https://www.globallogic.com/career-search-page/?country=india",
                "https://kpitcareers.kpit.com/", "https://jobs.brillio.com/"]:
        pg = b.new_page()
        calls = []
        pg.on("response", lambda r, c=calls: c.append((r.status, r.request.method, r.url[:120]))
              if r.request.resource_type in ("xhr", "fetch") else None)
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print("BROWSER goto err", url, str(e)[:100])
        t = ""
        for _ in range(8):
            pg.wait_for_timeout(4000)
            try:
                t = pg.content()
                if "Just a moment" not in t:
                    break
            except Exception:
                pass
        sig = sorted(set(re.findall(SIGS, t)))[:10]
        print(f"BROWSER {url[:70]} -> {pg.url[:70]} title={pg.title()[:50]!r} len={len(t)}\n      {sig}")
        for c in calls[:15]:
            print("      xhr", c)
        ref = re.search(r'"refNum":"([^"]+)"', t)
        if ref and "cognizant" in url:
            body = {"lang": "en_global", "deviceType": "desktop", "country": "global",
                    "pageName": "search-results", "ddoKey": "refineSearch", "from": 0,
                    "jobs": True, "counts": False, "all_fields": ["country"], "size": 10,
                    "clearAll": False, "jdsource": "facets", "isSliderEnable": False,
                    "pageId": (re.search(r'"pageId":"([^"]+)"', t) or [None, "page1"])[1],
                    "siteType": "external", "keywords": "", "global": True,
                    "selected_fields": {"country": ["India"]}, "locationData": {},
                    "refNum": ref.group(1)}
            res = pg.evaluate("""async (body) => {
                const r = await fetch('/widgets', {method: 'POST',
                    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
                return [r.status, (await r.text()).slice(0, 600)];
            }""", body)
            print("      in-page /widgets:", res[0], res[1][:400])
            try:
                d = json.loads(res[1] if res[1].endswith("}") else "{}")
                print("      totalHits:", (d.get("refineSearch") or {}).get("totalHits"))
            except Exception:
                pass
        pg.close()
    b.close()
