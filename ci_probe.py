#!/usr/bin/env python3
"""TEMPORARY - inspect the structure of Cognizant's XML job feed."""
import collections
import re
import xml.etree.ElementTree as ET

import requests

import jobscan as J

FEED = "https://careers.cognizant.com/india-en/jobs/xml/?rss=true"
raw = requests.get(FEED, headers=J.HEADERS, timeout=60)
print("feed", raw.status_code, len(raw.content))
print(raw.text[:600])
root = ET.fromstring(raw.content)
tags = collections.Counter(el.tag for el in root.iter())
print("tags:", tags.most_common(40))
first = next((el for el in root.iter("job")), None)
if first is not None:
    print("first record tag:", first.tag)
    for child in first:
        print("   ", child.tag, child.attrib, repr((child.text or "")[:200]))
countries = collections.Counter((el.text or "").strip() for el in root.iter() if re.search("country", el.tag, re.I))
print("countries:", countries.most_common(8))
