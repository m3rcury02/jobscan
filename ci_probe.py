#!/usr/bin/env python3
"""TEMPORARY - verify the Cognizant RSS source from the Actions runner."""
import requests

import jobscan as J

FEED = "https://careers.cognizant.com/india-en/jobs/xml/?rss=true"
raw = requests.get(FEED, headers=J.HEADERS, timeout=30)
print("feed", raw.status_code, raw.headers.get("content-type"), len(raw.text))
i = raw.text.find("<item")
print(raw.text[i:i + 1500])
jobs = J.fetch_rss(FEED, "India")
print("items", len(jobs), "eng", sum(J.title_ok(j["title"]) for j in jobs),
      "dated", sum(1 for j in jobs if j["posted"]))
for j in jobs[:5]:
    print("  ", j["title"][:50], "|", j["location"], "|", j["posted"], "|", j["url"][:90])
sample = [j for j in jobs if J.title_ok(j["title"])][:3]
for j in sample:
    j["company"] = "Cognizant"
J.enrich_all(sample)
for j in sample:
    print("  JD", j["title"][:40], len(j["description"]), J.band_label(J.yoe_band(j["title"], j["description"])),
          j.get("_enrich_error", ""), "|", j["description"][:200].replace("\n", " "))
html = requests.get("https://careers.cognizant.com/india-en/jobs/", headers=J.HEADERS, timeout=30).text
import re  # noqa: E402
print("listing page jobs:", len(set(re.findall(r'href="(/india-en/jobs/\d+/[^"]+)"', html))),
      "pager:", sorted(set(re.findall(r'[?&]page=(\d+)', html)))[-3:])
