#!/usr/bin/env python3
"""
jobscan.py - poll company ATS boards, filter to India + junior backend/AI,
score against a fixed profile, email a digest.

No API keys required for the job boards. All endpoints are public.
Email requires SMTP_USER / SMTP_PASS env vars (Gmail app password).

Usage:
    python jobscan.py                 # normal run, sends email
    python jobscan.py --dry-run       # print digest, send nothing
    python jobscan.py --reset         # wipe seen.json, treat everything as new
"""

import argparse
import csv
import html
import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from pathlib import Path

import requests

ROOT = Path(__file__).parent
COMPANIES_CSV = ROOT / "companies.csv"
SEEN_JSON = ROOT / "seen.json"
PIPELINE_CSV = ROOT / "pipeline.csv"
ERRORS_LOG = ROOT / "fetch_errors.log"

IST = timezone(timedelta(hours=5, minutes=30))
TIMEOUT = 25
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/140.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",   # no br: needs a package we do not install
    "Connection": "keep-alive",
}

# Only report roles posted within this many days. Override with --max-age.
MAX_AGE_DAYS = 2
# Boards fetched in parallel. Ashby 429s above ~8, and a throttled board comes
# back as a JSONDecodeError, so keep this conservative.
WORKERS = 8
# --backlog window. Beyond ~90 days a posting is usually a req the board never
# closed rather than a live opening.
BACKLOG_MAX_AGE = 90
# How deep to page a single Workday board. Only the largest tenants reach it.
WORKDAY_MAX = 600

# --------------------------------------------------------------------------
# PROFILE - edit this when your stack changes
# --------------------------------------------------------------------------

CORE_SKILLS = {
    "java": 3, "spring boot": 3, "spring": 2, "spring security": 2,
    "spring data": 2, "hibernate": 2, "jpa": 2, "rest api": 2,
    "restful": 2, "microservice": 3, "postgresql": 3, "postgres": 3,
    "redis": 2, "kafka": 3, "rabbitmq": 2, "docker": 2, "kubernetes": 2,
    "aws": 2, "github actions": 1, "ci/cd": 1, "junit": 1, "mockito": 1,
    "sql": 1, "linux": 1, "maven": 1, "gradle": 1,
}

SECONDARY_SKILLS = {
    "typescript": 2, "node.js": 2, "nodejs": 2, "nestjs": 2, "nest.js": 2,
    "express": 1, "react": 2, "python": 3, "fastapi": 3, "django": 2,
    "javascript": 1, "websocket": 1, "grpc": 1, "terraform": 1,
}

AI_SKILLS = {
    "rag": 3, "retrieval-augmented": 3, "retrieval augmented": 3,
    "pgvector": 3, "vector database": 2, "vector search": 2,
    "embedding": 2, "reranking": 3, "rerank": 2, "langchain": 2,
    "langgraph": 3, "llm": 2, "large language model": 2,
    "tool calling": 3, "agentic": 2, "semantic search": 2,
    "prompt engineering": 1, "openai": 1, "hybrid retrieval": 3,
}

ALL_SKILLS = {**CORE_SKILLS, **SECONDARY_SKILLS, **AI_SKILLS}
MAX_SKILL_POINTS = 34  # tuned so a strong match lands near 100

# --------------------------------------------------------------------------
# FILTERS
# --------------------------------------------------------------------------

INDIA_CITIES = [
    "bengaluru", "bangalore", "hyderabad", "pune", "chennai", "gurugram",
    "gurgaon", "noida", "delhi", "ncr", "mumbai", "kolkata", "ahmedabad",
    "kochi", "cochin", "trivandrum", "thiruvananthapuram", "coimbatore",
    "indore", "jaipur", "chandigarh", "vadodara", "nagpur", "mysuru",
    "mysore", "bhubaneswar", "vishakhapatnam", "vizag",
]

NON_INDIA_HINTS = [
    "united states", "usa", "canada", "united kingdom", "london",
    "germany", "berlin", "france", "paris", "netherlands", "amsterdam",
    "singapore", "australia", "sydney", "japan", "tokyo", "dublin",
    "poland", "warsaw", "spain", "madrid", "brazil", "mexico",
    "israel", "tel aviv", "dubai", "uae", "philippines", "manila",
    "vietnam", "indonesia", "china", "shanghai", "korea", "seoul",
    # region labels, so a scoped "Remote" is caught before is_india() sees it
    "emea", "europe", "latam", "north america", "south america", "americas",
    "united kingdom", "us", "uk", "eu", "canada", "mexico",
    # countries and regions that turned up attached to "Remote" in real postings
    "sweden", "ireland", "chile", "argentina", "colombia", "peru", "portugal",
    "italy", "norway", "denmark", "finland", "switzerland", "austria",
    "belgium", "czechia", "czech republic", "romania", "bulgaria", "greece",
    "turkey", "egypt", "nigeria", "kenya", "south africa", "new zealand",
    "thailand", "malaysia", "taiwan", "hong kong", "pakistan", "bangladesh",
    "sri lanka", "nepal", "amer", "anz", "emea", "apj", "nam",
    # US/Canada metros, for "Remote, San Francisco, CA" style labels
    "san francisco", "san jose", "sunnyvale", "palo alto", "mountain view",
    "santa clara", "san diego", "los angeles", "seattle", "portland",
    "denver", "austin", "boston", "chicago", "atlanta", "phoenix", "dallas",
    "houston", "miami", "toronto", "vancouver", "montreal",
]

# Patterns that word-boundary hints cannot catch: "U.S. Remote", "Remote, WA",
# and US state names that never appear as a bare country word.
NON_INDIA_RE = re.compile(
    r"\bu\.\s?s\.?\s?a?\b"
    r"|\b(alabama|alaska|arizona|arkansas|california|colorado|connecticut"
    r"|delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas"
    r"|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota"
    r"|mississippi|missouri|montana|nebraska|nevada|hampshire|jersey"
    r"|new york|carolina|dakota|ohio|oklahoma|oregon|pennsylvania|rhode island"
    r"|tennessee|texas|utah|vermont|virginia|washington|wisconsin|wyoming"
    r"|washington,? d\.?c\.?)\b",
    re.I)

TITLE_KEEP = [
    "backend", "back-end", "back end", "software engineer",
    "software developer", "platform engineer", "full stack", "fullstack",
    "full-stack", "ai engineer", "ml engineer", "machine learning engineer",
    "applied ai", "api engineer", "sde", "member of technical staff",
    "application engineer", "server engineer", "infrastructure engineer",
    # widened for recall: these were rejecting ~280 live India roles a run
    "developer", "data engineer", "devops", "site reliability", "sre",
    "cloud engineer", "solutions engineer", "integration engineer",
    "systems engineer", "engineer ii", "engineer i", "engineer 2",
    "engineer 3", "engineer iii", "associate engineer", "graduate engineer",
    "programmer", "sdet", "software eng", "engineer -", "engineer,",
]

TITLE_DROP = [
    "staff", "principal", "lead", "manager", "director", "architect",
    "head of", "vp ", "vice president", "intern", "internship",
    "president", "chief", "fellow", "distinguished", "senior staff",
]

# Campus hiring drives: "IIT Jammu 2026 || TravClan || SDE-1" and similar.
CAMPUS_DRIVE = re.compile(r"\d{4}\s*\|\||\|\|\s*\d{4}|campus\s+(drive|hiring)", re.I)

COMPANY_DROP = [
    "accenture", "tcs", "tata consultancy", "infosys", "wipro",
    "cognizant", "capgemini", "hcl", "tech mahindra", "ltimindtree",
    "mindtree", "mphasis", "birlasoft", "hexaware", "zensar",
    "randstad", "adecco", "manpower", "michael page", "robert half",
    "staffing", "recruitment", "consultancy services", "talent solutions",
]

# Years-of-experience patterns. If minimum > MAX_YOE, drop.
MAX_YOE = 5
YOE_PATTERNS = [
    re.compile(r"(\d+)\s*\+?\s*(?:to|-|–)\s*\d+\s*\+?\s*years?", re.I),
    re.compile(r"(?:minimum|min\.?|at least)\s*(?:of\s*)?(\d+)\s*\+?\s*years?", re.I),
    re.compile(r"(\d+)\s*\+\s*years?", re.I),
]

# --------------------------------------------------------------------------
# HELPERS
# --------------------------------------------------------------------------


def log_error(company, msg):
    line = f"{datetime.now(IST).isoformat()}\t{company}\t{msg}\n"
    with open(ERRORS_LOG, "a", encoding="utf-8") as f:
        f.write(line)
    print(f"  ! {company}: {msg}", file=sys.stderr)


def strip_html(text):
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|h[1-6]|div|li|tr|td|section|article)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def age_days(posted):
    """Days since posting. None when the board gave no usable date."""
    if not posted:
        return None
    try:
        d = datetime.strptime(posted[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    delta = (datetime.now(timezone.utc) - d).days
    return max(delta, 0)


def get_json(url, retries=2):
    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    raise last


# --------------------------------------------------------------------------
# ATS ADAPTERS - each returns a list of normalised job dicts
# --------------------------------------------------------------------------


def fetch_greenhouse(token, tenant=None):
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    data = get_json(url)
    out = []
    for j in data.get("jobs", []):
        out.append({
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "description": strip_html(j.get("content", "")),
            "posted": (j.get("first_published") or j.get("updated_at") or "")[:10],
        })
    return out


def fetch_lever(token, tenant=None):
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    data = get_json(url)
    out = []
    for j in data:
        cat = j.get("categories") or {}
        ts = j.get("createdAt")
        posted = ""
        if ts:
            posted = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        out.append({
            "title": j.get("text", ""),
            "location": cat.get("location", "") or "",
            "url": j.get("hostedUrl", ""),
            "description": strip_html(j.get("descriptionPlain") or j.get("description", "")),
            "posted": posted,
        })
    return out


def fetch_ashby(token, tenant=None):
    url = (f"https://api.ashbyhq.com/posting-api/job-board/{token}"
           f"?includeCompensation=true")
    data = get_json(url)
    out = []
    for j in data.get("jobs", []):
        out.append({
            "title": j.get("title", ""),
            "location": j.get("location", "") or "",
            "url": j.get("jobUrl", ""),
            "description": strip_html(j.get("descriptionHtml") or j.get("descriptionPlain", "")),
            "posted": (j.get("publishedAt") or "")[:10],
        })
    return out


def fetch_smartrecruiters(token, tenant=None):
    out = []
    offset = 0
    while True:
        url = (f"https://api.smartrecruiters.com/v1/companies/{token}"
               f"/postings?limit=100&offset={offset}")
        data = get_json(url)
        items = data.get("content", [])
        if not items:
            break
        for j in items:
            loc = j.get("location") or {}
            loc_str = ", ".join(
                x for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x
            )
            out.append({
                "title": j.get("name", ""),
                "location": loc_str,
                "url": j.get("ref", "").replace(
                    "api.smartrecruiters.com/v1/companies",
                    "jobs.smartrecruiters.com"
                ) or f"https://jobs.smartrecruiters.com/{token}/{j.get('id')}",
                "description": "",  # detail call needed; title/location suffice for triage
                "posted": (j.get("releasedDate") or "")[:10],
            })
        offset += 100
        if offset >= data.get("totalFound", 0):
            break
    return out


def fetch_workable(token, tenant=None):
    url = f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"
    data = get_json(url)
    out = []
    for j in data.get("jobs", []):
        loc = ", ".join(x for x in [j.get("city"), j.get("state"), j.get("country")] if x)
        out.append({
            "title": j.get("title", ""),
            "location": loc,
            "url": j.get("url") or j.get("application_url", ""),
            "description": strip_html(j.get("description", "") + " " + j.get("requirements", "")),
            "posted": (j.get("published_on") or "")[:10],
        })
    return out


def fetch_oracle(token, tenant=None):
    """token = siteNumber (usually CX_1). tenant = full host, e.g.
    eeho.fa.us2.oraclecloud.com"""
    if not tenant:
        raise ValueError("oracle rows need a Tenant value")
    site = token or "CX_1"
    url = (f"https://{tenant}/hcmRestApi/resources/latest/"
           f"recruitingCEJobRequisitions?onlyData=true&expand=requisitionList"
           f"&finder=findReqs;siteNumber={site},limit=200,location=India")
    data = get_json(url)
    out = []
    for block in data.get("items", []):
        for j in block.get("requisitionList", []):
            rid = j.get("Id", "")
            out.append({
                "title": j.get("Title", ""),
                "location": j.get("PrimaryLocation", "") or "",
                "url": f"https://{tenant}/hcmUI/CandidateExperience/en/sites/{site}/job/{rid}",
                "description": strip_html(j.get("ShortDescriptionStr", "")),
                "posted": (j.get("PostedDate") or "")[:10],
            })
    return out


RELATIVE_DATE = re.compile(
    r"(\d+)\s*\+?\s*(day|days|hour|hours|week|weeks|month|months)\s*ago", re.I)


def _relative_posted(chunk):
    """Turn 'Posted 5 days ago' into a YYYY-MM-DD string. Keka, Workday and
    several Indian careers pages publish recency this way, not as a date."""
    if not chunk:
        return ""
    low = chunk.lower()
    if "today" in low or "just posted" in low:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if "yesterday" in low:
        return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    m = RELATIVE_DATE.search(chunk)
    if not m:
        return ""
    n, unit = int(m.group(1)), m.group(2).lower()
    days = {"hour": 0, "hours": 0, "day": 1, "days": 1,
            "week": 7, "weeks": 7, "month": 30, "months": 30}[unit]
    when = datetime.now(timezone.utc) - timedelta(days=n * days)
    return when.strftime("%Y-%m-%d")


def fetch_custom(token, tenant=None):
    """Companies with no ATS. Token = the full careers page URL.

    Plain GET, then keyword-match title-shaped lines in the page text. Crude by
    design: no per-site selectors to maintain. Boards give no posting date, so
    these are dated by FIRST SIGHTING instead - see run().
    """
    url = token if token.startswith("http") else f"https://{token}"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    text = strip_html(r.text)

    out, seen_titles = [], set()
    lines = [ln.strip() for ln in text.split("\n")]
    for i, ln in enumerate(lines):
        if not (4 < len(ln) < 90):
            continue
        low = ln.lower()
        if not any(k in low for k in TITLE_KEEP):
            continue
        # reject prose: requirement bullets and sentences are not job titles
        if ln.rstrip().endswith((".", ":", ";", "?")):
            continue
        if len(ln.split()) > 12:
            continue
        if re.match(r"^[\d\u2022\-*]", ln.strip()):
            continue
        if re.search(r"\b(experience|years?|responsib|ability|familiar|"
                     r"proficien|knowledge|understand|must have|should have|"
                     r"you will|we are looking)\b", low):
            continue
        if ln in seen_titles:
            continue
        seen_titles.add(ln)

        # location: same line, or within the next two lines
        loc = ""
        for cand in [ln] + lines[i + 1:i + 3]:
            cl = cand.lower()
            if _has_india_city(cl) or _word("india", cl):
                for city in INDIA_CITIES:
                    m = re.search(r"\b" + re.escape(city) + r"\b", cand, re.I)
                    if m:
                        loc = cand[m.start():m.start() + 40].strip(" -|,")
                        break
                loc = loc or "India"
                break
        if not loc:
            loc = "India"

        # some pages publish "N days ago" near the title - use it if present
        posted = _relative_posted(" ".join(lines[i:i + 4]))

        # strip the date phrase and any trailing location out of the title
        title = RELATIVE_DATE.sub(" ", ln)
        for city in INDIA_CITIES:
            m = re.search(r"\b" + re.escape(city) + r"\b", title, re.I)
            if m:
                title = title[:m.start()]
                break
        title = re.sub(r"\s{2,}", " ", title).strip(" -|,\u2022\t")
        if not title:
            continue

        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]
        out.append({
            "title": title,
            "location": loc,
            "url": f"{url}#{slug}",
            "description": "",
            "posted": posted,      # empty means first-seen dating applies
        })
    return out


KEKA_ID = re.compile(r"identifier:\s*['\"]([0-9a-f-]{36})['\"]")
KEKA_PORTAL = re.compile(r"portalName:\s*['\"]([^'\"]+)['\"]")
KEKA_INNER = re.compile(r"fetch\(['\"]([^'\"]+careerportal[^'\"]+)['\"]")
KEKA_META_PORTAL = re.compile(r'<meta\s+name=["\']portalName["\']\s*content=["\']([^"\']*)["\']', re.I)


def fetch_keka(token, tenant=None):
    """Keka ATS. Token = subdomain (e.g. 'gokwik') or the full careers URL.

    The careers page is an empty JS shell, which is why the generic custom
    scraper found nothing on these boards. Two template generations exist:

      old: /careers/ -> fetches an embedjobs hash page carrying a GUID
           `identifier`, then /careers/api/embedjobs/<portal>/active/<id>
      new (cdn.keka.com/careers/v/2026/): no GUID at all - the portal name
           alone (from <meta name="portalName">, or "default") is the whole
           key: /careers/api/jobs/<portal>/active

    Try the old scheme first since most tenants are still on it; fall back
    to the new one when no GUID is present.
    """
    sub = token.strip()
    if sub.startswith("http"):
        sub = re.sub(r"^https?://([^.]+)\.keka\.com.*$", r"\1", sub.rstrip("/"))
    base = f"https://{sub}.keka.com"

    shell = requests.get(f"{base}/careers/", headers=HEADERS, timeout=TIMEOUT)
    shell.raise_for_status()
    m = KEKA_INNER.search(shell.text)
    page = shell.text
    if m:
        inner = m.group(1)
        inner = base + inner if inner.startswith("/") else inner
        r = requests.get(inner, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        page = r.text

    mid = KEKA_ID.search(page)
    if mid:
        ident = mid.group(1)
        mp = KEKA_PORTAL.search(page)
        portal = mp.group(1) if mp else "default"
        data = get_json(f"{base}/careers/api/embedjobs/{portal}/active/{ident}")
    else:
        mp = KEKA_META_PORTAL.search(shell.text)
        portal = (mp.group(1) if mp else "").strip() or "default"
        data = get_json(f"{base}/careers/api/jobs/{portal}/active")

    out = []
    for j in data:
        locs = []
        for l in j.get("jobLocations") or []:
            city = (l.get("city") or l.get("name") or "").strip()
            country = (l.get("countryName") or "").strip()
            locs.append(", ".join(x for x in (city, country) if x))
        # experience reads like "2 - 4 Years"; append it so min_yoe can see it
        exp = (j.get("experience") or "").strip()
        desc = strip_html(j.get("description") or "")
        out.append({
            "title": (j.get("title") or "").strip(),
            "location": "; ".join(dict.fromkeys(locs)),
            "url": f"{base}/careers/jobdetails/{j.get('jobNumber') or j.get('id')}",
            "description": (f"Experience: {exp}\n{desc}" if exp else desc),
            "posted": (j.get("publishedOn") or "")[:10],
        })
    return out


def _wd_bullet_location(bullets):
    """Last bulletField is the location when locationsText is missing. The
    first is a requisition id like R00356610, so never take a lone bullet."""
    if not bullets or len(bullets) < 2:
        return ""
    tail = str(bullets[-1]).strip()
    return "" if re.fullmatch(r"[A-Z]{1,3}\d{4,}", tail) else tail


def fetch_workday(token, tenant=None):
    """Workday. Token = site name (e.g. Cisco_Careers).
    Tenant = host prefix including the wd number (e.g. cisco.wd5).

    Workday's job list is a POST endpoint, not a GET, which is why it needs its
    own adapter. Dates come back relative ("Posted 3 Days Ago").
    """
    if not tenant:
        raise ValueError("workday rows need a Tenant like 'cisco.wd5'")
    host = f"https://{tenant}.myworkdayjobs.com"
    api = f"{host}/wday/cxs/{tenant.split('.')[0]}/{token}/jobs"
    hdrs = {**HEADERS, "Content-Type": "application/json",
            "Accept": "application/json",
            # Workday sits behind an edge that is happier with a same-origin
            # looking request. It is not what causes a 422 - that is the tenant
            # not living on the shard - but it is the correct thing to send.
            "Origin": host}

    # 600 not 200: big tenants order their India results by department, so
    # Accenture's engineering roles do not start until around offset 80 and
    # run past 600. Boards smaller than this break out early on an empty page.
    out, offset, total = [], 0, 0
    while offset < WORKDAY_MAX:
        body = {"appliedFacets": {}, "limit": 20, "offset": offset,
                "searchText": "India"}
        r = requests.post(api, json=body, headers=hdrs, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        posts = data.get("jobPostings", [])
        if not posts:
            break
        # Accenture reports the real total on page 1 and 0 on every page
        # after, so trusting each page's total truncates the board at 40.
        total = max(total, data.get("total") or 0)
        for j in posts:
            path = j.get("externalPath", "")
            out.append({
                "title": j.get("title", ""),
                # Some tenants (Accenture among them) omit locationsText
                # entirely and put the location last in bulletFields, after
                # the requisition id.
                "location": (j.get("locationsText") or "").strip()
                            or _wd_bullet_location(j.get("bulletFields")),
                "url": f"{host}/en-US/{token}{path}",
                "description": " ".join(j.get("bulletFields") or []),
                "posted": _relative_posted(j.get("postedOn", "")),
            })
        offset += 20
        if len(posts) < 20 or (total and offset >= total):
            break
    return out


AMAZON_URL = "https://www.amazon.jobs/en/search.json"


def fetch_amazon(token="", tenant=None):
    """Amazon / AWS. Public JSON, no key. Token is unused.

    loc_query is fuzzy - "India" returns Sydney - so filter on the
    normalized_country_code facet instead, which is exact.
    """
    out, offset = [], 0
    while offset < 500:
        params = {
            "normalized_country_code[]": "IND",
            "result_limit": 100,
            "offset": offset,
            "sort": "recent",
        }
        r = requests.get(AMAZON_URL, params=params, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        jobs = data.get("jobs") or []
        if not jobs:
            break
        for j in jobs:
            if (j.get("country_code") or "").upper() != "IND":
                continue
            posted = ""
            raw = (j.get("posted_date") or "").strip()
            for fmt in ("%B %d, %Y", "%Y-%m-%d"):
                try:
                    posted = datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    pass
            # basic_qualifications carries the YOE line min_yoe() looks for
            desc = " ".join(x for x in (j.get("basic_qualifications"),
                                        j.get("description")) if x)
            out.append({
                "title": j.get("title", ""),
                "location": j.get("normalized_location") or j.get("location", ""),
                "url": "https://www.amazon.jobs" + (j.get("job_path") or ""),
                "description": strip_html(desc),
                "posted": posted,
            })
        offset += 100
        if offset >= (data.get("hits") or 0):
            break
    return out


def fetch_eightfold(token, tenant=None):
    """Eightfold career sites. Token = subdomain, Tenant = the domain= param.

    Public JSON, no key, and t_create is a real posting timestamp - which
    matters because the alternative for these companies was scraping a
    rendered page that carries no dates at all.
    """
    sub = token.strip()
    dom = (tenant or "").strip()
    base = f"https://{sub}.eightfold.ai/api/apply/v2/jobs"
    out, start = [], 0
    while start < 500:
        url = f"{base}?start={start}&num=100&location=India"
        if dom:
            url += f"&domain={dom}"
        data = get_json(url)
        pos = data.get("positions") or []
        if not pos:
            break
        for j in pos:
            posted = ""
            ts = j.get("t_create")
            if ts:
                try:
                    posted = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
                except (ValueError, OSError):
                    pass
            locs = j.get("locations") or ([j["location"]] if j.get("location") else [])
            url_j = (j.get("canonicalPositionUrl")
                     or f"https://{sub}.eightfold.ai/careers/job?pid={j.get('id')}")
            out.append({
                "title": j.get("name", ""),
                "location": "; ".join(dict.fromkeys(str(x) for x in locs)),
                "url": url_j,
                "description": strip_html(j.get("job_description") or ""),
                "posted": posted,
            })
        # advance by what came back, not what was asked for: the API caps the
        # page size well below num, and stepping by num skips the difference
        start += len(pos)
        if start >= (data.get("count") or 0):
            break
    return out


SF_ROW = re.compile(r'class="data-row"(.*?)(?=class="data-row"|</tbody>)', re.S)
SF_TILE = re.compile(r'<li class="job-tile\b(.*?)</li>', re.S)
# jobTitle-link is rarely the whole class attribute: CommScope serves
# class="jobTitle-link fontcolor472182c9d6801de7". Match it as one class
# among several, in either attribute order.
SF_LINK = re.compile(
    r'class="[^"]*\bjobTitle-link\b[^"]*"[^>]*?href="([^"]+)"[^>]*>(.*?)</a>'
    r'|href="([^"]+)"[^>]*?class="[^"]*\bjobTitle-link\b[^"]*"[^>]*>(.*?)</a>', re.S)
SF_LOC = re.compile(r'class="[^"]*jobLocation[^"]*"[^>]*>(.*?)</span>', re.S)
SF_DATE = re.compile(r'class="[^"]*jobDate[^"]*"[^>]*>(.*?)</span>', re.S)
SF_TOTAL = re.compile(r'aria-label="Results \d+\s*[\u2013-]\s*(\d+)"'
                      r'|data-record-returned="(\d+)"')


def fetch_successfactors(token, tenant=None):
    """SAP SuccessFactors Recruiting Marketing sites (jobs.<company>.com).

    Server-rendered, so no JS needed, but two templates are in the wild: an
    older table of data-row cells and a newer job-tile list. Swiss Re and Festo
    serve the first, CommScope and Marelli the second, so both are parsed.

    locationsearch=India filters at the source. That is also why a row with no
    parseable location falls back to "India" rather than being dropped - the
    query already constrained it.
    """
    host = token.strip().replace("https://", "").replace("http://", "").strip("/")
    base = f"https://{host}"
    out, startrow, seen_urls = [], 0, set()
    while startrow < 400:
        url = f"{base}/search/?q=&locationsearch=India&startrow={startrow}"
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        chunks = SF_ROW.findall(r.text) or SF_TILE.findall(r.text)
        if not chunks:
            break
        before = len(out)
        for chunk in chunks:
            m = SF_LINK.search(chunk)
            if not m:
                continue
            href = m.group(1) or m.group(3)
            title = m.group(2) or m.group(4)
            href = html.unescape(href or "")
            if not href or href in seen_urls:
                continue
            seen_urls.add(href)
            loc = SF_LOC.search(chunk)
            dt = SF_DATE.search(chunk)
            posted = ""
            if dt:
                raw = strip_html(dt.group(1)).strip()
                for fmt in ("%b %d, %Y", "%d %b %Y", "%Y-%m-%d", "%d-%b-%Y"):
                    try:
                        posted = datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        pass
            out.append({
                "title": strip_html(title).strip(),
                "location": (strip_html(loc.group(1)).strip() if loc else "") or "India",
                "url": base + href,
                "description": "",
                "posted": posted,
            })
        if len(out) == before:
            break
        startrow += len(chunks)
        t = SF_TOTAL.search(r.text)
        if t and startrow >= int(t.group(1) or t.group(2)):
            break
    return out


def fetch_pinpoint(token, tenant=None):
    """Pinpoint ATS. Token = subdomain, e.g. hiverhq.

    Public JSON at /postings.json, no key. The board carries no posting date,
    so these fall back to first-sighting dating in run().
    """
    sub = token.strip().replace("https://", "").split(".")[0]
    data = get_json(f"https://{sub}.pinpointhq.com/postings.json")
    out = []
    for j in (data.get("data") if isinstance(data, dict) else data) or []:
        loc = j.get("location") or {}
        if isinstance(loc, dict):
            parts = [loc.get("name") or "", loc.get("city") or "",
                     loc.get("country") or ""]
            loc = ", ".join(dict.fromkeys(x for x in parts if x))
        out.append({
            "title": j.get("title", ""),
            "location": str(loc),
            "url": j.get("url") or f"https://{sub}.pinpointhq.com{j.get('path','')}",
            "description": strip_html(" ".join(
                str(j.get(k) or "") for k in
                ("description", "key_responsibilities", "skills_knowledge_expertise"))),
            "posted": "",
        })
    return out


def fetch_agency(token, tenant=None):
    """Recruitment consultancies and staffing firms. Same scrape as custom, but
    flagged: the hiring company is not named, so these cannot be scored or
    researched and must never be treated as employer postings."""
    jobs = fetch_custom(token, tenant)
    for j in jobs:
        j["agency"] = True
    return jobs


# --------------------------------------------------------------------------
# FIRECRAWL - JS-rendered / bot-shy careers pages
# --------------------------------------------------------------------------
# Token = full careers search URL (already filtered to India + keyword where the
# site allows it). Needs FIRECRAWL_API_KEY. Uses markdown mode (1 credit/page)
# and reads [title](url) links, so each role keeps its own URL.
#
# Cost control: these rows are only fetched on the UTC hours listed in
# FIRECRAWL_HOURS_UTC (default "2" = once a day, 07:30 IST), or on any
# non-scan mode. 5 boards x 30 days = ~150 credits/month.

FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"
MD_LINK = re.compile(r"\[((?:[^\[\]]|\\\[|\\\])+?)\]\((https?://[^)\s]+)\)")


def _firecrawl_due():
    if os.environ.get("MODE", "scan") != "scan":
        return True
    hours = os.environ.get("FIRECRAWL_HOURS_UTC", "2")
    now = datetime.now(timezone.utc).hour
    return str(now) in [h.strip() for h in hours.split(",")]


def parse_markdown_jobs(md, base_url=""):
    """Pull job-shaped links out of Firecrawl markdown. Pure function, testable."""
    out, seen_urls = [], set()
    for m in MD_LINK.finditer(md):
        raw, url = m.group(1), m.group(2)
        # card links pack title + location into the text, split by escaped newlines
        parts = [p.strip(" *#\\") for p in re.split(r"(?:\\\\|\\n|\n)+", raw)]
        parts = [p for p in parts if p]
        if not parts:
            continue
        title = re.sub(r"\\(.)", r"\1", parts[0])          # markdown escapes
        title = re.sub(r"\s{2,}", " ", title).strip()
        low = title.lower()
        if not (4 < len(title) < 110):
            continue
        if not any(k in low for k in TITLE_KEEP):
            continue
        if url in seen_urls or re.search(r"(apply|login|sign-?in|alert)", url, re.I) and "job" not in url.lower():
            continue
        seen_urls.add(url)
        rest = " ".join(parts[1:])
        loc = ""
        for city in INDIA_CITIES:
            if re.search(r"\b" + re.escape(city) + r"\b", rest + " " + md[m.end():m.end() + 160], re.I):
                loc = city.title()
                break
        out.append({
            "title": title,
            "location": loc or "India",
            "url": url,
            "description": "",
            "posted": _relative_posted(rest),
        })
    return out


def fetch_firecrawl(token, tenant=None):
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        raise ValueError("FIRECRAWL_API_KEY not set")
    if not _firecrawl_due():
        return []          # not an error: skipped to save credits this run
    wait = int(tenant) if (tenant or "").isdigit() else 6000
    r = requests.post(
        FIRECRAWL_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"url": token, "formats": ["markdown"], "onlyMainContent": True,
              "waitFor": wait, "maxAge": 0},
        timeout=90,
    )
    r.raise_for_status()
    body = r.json()
    md = (body.get("data") or {}).get("markdown", "")
    if not md:
        raise ValueError("firecrawl returned no markdown")
    jobs = parse_markdown_jobs(md, token)
    for j in jobs:
        j["firecrawl"] = True
    return jobs


ADAPTERS = {
    "amazon": fetch_amazon,
    "eightfold": fetch_eightfold,
    "pinpoint": fetch_pinpoint,
    "successfactors": fetch_successfactors,
    "keka": fetch_keka,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workable": fetch_workable,
    "oracle": fetch_oracle,
    "custom": fetch_custom,
    "agency": fetch_agency,
    "workday": fetch_workday,
    "firecrawl": fetch_firecrawl,
}

# --------------------------------------------------------------------------
# FILTER + SCORE
# --------------------------------------------------------------------------


def _word(term, text):
    """Word-boundary match. Stops 'Indianapolis' matching 'India'."""
    return re.search(r"\b" + re.escape(term) + r"\b", text) is not None


def _has_india_city(loc):
    return any(_word(city, loc) for city in INDIA_CITIES)


def is_india(location):
    loc = (location or "").lower()
    if not loc:
        return False
    if _word("india", loc) or _word("bharat", loc):
        return True
    if _has_india_city(loc):
        return True
    if _word("in", loc) and not any(h in loc for h in NON_INDIA_HINTS):
        return True
    if "remote" in loc:
        # A bare "Remote" names no country, so treat it as India-eligible.
        # run() calls is_non_india_only() first, which rejects "Remote - US",
        # "Remote (Europe)" and kin before this is reached. What survives is
        # genuinely unscoped: globally-open, or open and simply not labelled.
        # Some of those will still turn out to be US-only once you read the
        # posting - that is the cost of not missing the ones that are not.
        return True
    return False


def is_non_india_only(location):
    loc = (location or "").lower()
    if _has_india_city(loc) or _word("india", loc):
        return False
    # word-boundary, not substring: "us" has to be the token US, or "Austin"
    # and "Belarus" would read as United States.
    if NON_INDIA_RE.search(loc):
        return True
    return any(_word(hint, loc) for hint in NON_INDIA_HINTS)


def title_ok(title):
    t = (title or "").lower()
    if CAMPUS_DRIVE.search(title or ""):
        return False
    if any(_word(bad.strip(), t) for bad in TITLE_DROP):
        return False
    return any(good in t for good in TITLE_KEEP)


def company_ok(company, referral=False):
    """COMPANY_DROP exists to keep mass-market service firms out of the digest.
    A referral changes that calculus entirely - a role you can be walked into
    is worth seeing whoever posted it - so a Referral row skips the check.
    """
    if referral:
        return True
    c = (company or "").lower()
    return not any(bad in c for bad in COMPANY_DROP)


def min_yoe(description):
    """Lowest 'minimum years' figure found. None if nothing parseable."""
    if not description:
        return None
    found = []
    for pat in YOE_PATTERNS:
        for m in pat.finditer(description[:6000]):
            try:
                found.append(int(m.group(1)))
            except (ValueError, IndexError):
                pass
    return min(found) if found else None


def score(job):
    """0-100 fit score plus reason and gap strings."""
    blob = f"{job['title']} {job['description']}".lower()

    hits = []
    points = 0
    for skill, weight in ALL_SKILLS.items():
        if skill in blob:
            points += weight
            hits.append((weight, skill))
    skill_score = min(points / MAX_SKILL_POINTS, 1.0) * 50

    yoe = job.get("min_yoe")
    if yoe is None:
        yoe_score = 20            # unstated, assume open
    elif yoe <= 2:
        yoe_score = 30
    elif yoe <= 4:
        yoe_score = 24
    elif yoe == 5:
        yoe_score = 12
    else:
        yoe_score = 0

    t = job["title"].lower()
    if any(k in t for k in ["backend", "back-end", "back end"]):
        role_score = 20
    elif any(k in t for k in ["ai engineer", "ml engineer", "applied ai", "machine learning"]):
        role_score = 20
    elif any(k in t for k in ["full stack", "fullstack", "full-stack", "platform"]):
        role_score = 16
    elif any(k in t for k in ["software engineer", "software developer", "sde"]):
        role_score = 14
    else:
        role_score = 8

    total = round(skill_score + yoe_score + role_score)

    # collapse aliases so the reason line does not read "postgresql, postgres"
    aliases = {
        "postgres": "postgresql", "nodejs": "node.js", "nest.js": "nestjs",
        "retrieval augmented": "rag", "retrieval-augmented": "rag",
        "rerank": "reranking", "large language model": "llm",
        "back-end": "backend", "back end": "backend",
    }
    hits.sort(reverse=True)
    top, seen_alias = [], set()
    for _, s in hits:
        canon = aliases.get(s, s)
        if canon in seen_alias:
            continue
        seen_alias.add(canon)
        top.append(canon)
        if len(top) == 3:
            break
    reason = "Overlap: " + ", ".join(top) if top else "No direct stack overlap detected"

    if yoe is not None and yoe > 4:
        gap = f"Asks {yoe}+ years"
    elif not top:
        gap = "Stack not recognised from description"
    else:
        missing = [s for s in ["kubernetes", "kafka", "aws", "go", "scala", "rust"]
                   if s in blob and s not in ALL_SKILLS]
        gap = f"Watch: {', '.join(missing)}" if missing else "No blocking gap detected"

    return min(total, 100), reason, gap


# --------------------------------------------------------------------------
# PIPELINE
# --------------------------------------------------------------------------


def load_companies():
    if not COMPANIES_CSV.exists():
        print(f"Missing {COMPANIES_CSV}. See companies.csv template.", file=sys.stderr)
        sys.exit(1)
    rows = []
    with open(COMPANIES_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("Company", "").strip().startswith("#"):
                continue
            if not row.get("Token", "").strip():
                continue
            rows.append({k: (v or "").strip() for k, v in row.items()})
    return rows


def load_seen():
    if SEEN_JSON.exists():
        return set(json.loads(SEEN_JSON.read_text()))
    return set()


def save_seen(seen):
    SEEN_JSON.write_text(json.dumps(sorted(seen), indent=0))


def append_pipeline(rows):
    cols = ["DateSeen", "Company", "Title", "Location", "MinYOE", "Referral", "URL",
            "Posted", "FitScore", "FitReason", "GapNote", "Status",
            "AppliedDate", "FollowUpDate"]
    existing = None
    if PIPELINE_CSV.exists():
        with open(PIPELINE_CSV, newline="", encoding="utf-8") as f:
            existing = next(csv.reader(f), None)

    if existing:
        # Keep any column a human added by hand; never drop tracking data.
        cols = list(dict.fromkeys(cols + [c for c in existing if c not in cols]))
        if existing != cols:
            # Header gained a column. Appending wider rows under the old header
            # would shift every field right, so rewrite the file once instead.
            with open(PIPELINE_CSV, newline="", encoding="utf-8") as f:
                old_rows = list(csv.DictReader(f))
            with open(PIPELINE_CSV, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=cols)
                w.writeheader()
                for r in old_rows:
                    w.writerow({c: (r.get(c) or "") for c in cols})
            print(f"pipeline.csv migrated to {len(cols)} columns "
                  f"({len(old_rows)} rows preserved)")

    with open(PIPELINE_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if not existing:
            w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def run(dry_run=False, reset=False, max_age=MAX_AGE_DAYS):
    companies = load_companies()
    seen = set() if reset else load_seen()
    today = datetime.now(IST).strftime("%Y-%m-%d")

    raw = []
    errors = 0
    print(f"Polling {len(companies)} boards with {WORKERS} workers...")

    def poll(c):
        ats = c.get("ATS", "").lower()
        fn = ADAPTERS.get(ats)
        if not fn:
            return c, None, f"unknown ATS '{ats}'"
        try:
            jobs = fn(c["Token"], c.get("Tenant"))
            ref = (c.get("Referral") or "").strip().lower() in ("y", "yes", "1", "true")
            for j in jobs:
                j["company"] = c.get("Company") or c["Token"]
                j["referral"] = ref
            return c, jobs, None
        except Exception as e:
            return c, None, f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for c, jobs, err in pool.map(poll, companies):
            if err:
                log_error(c.get("Company", "?"), err)
                errors += 1
            else:
                raw.extend(jobs)

    kept, too_old, undated = [], 0, 0
    for j in raw:
        if not j.get("url") or j["url"] in seen:
            continue
        if not company_ok(j["company"], j.get("referral")):
            continue
        if not title_ok(j["title"]):
            continue
        if is_non_india_only(j["location"]):
            continue
        if not is_india(j["location"]):
            continue

        age = age_days(j.get("posted"))
        if age is None:
            undated += 1
            j["age"] = None
        elif max_age > 0 and age > max_age:
            # NOT added to seen: an old posting is still an open posting, and
            # marking it read here is what silently hid 500+ live roles.
            too_old += 1
            continue
        else:
            j["age"] = age

        j["min_yoe"] = min_yoe(f"{j['title']} {j['description']}")
        if j["min_yoe"] is not None and j["min_yoe"] > MAX_YOE:
            continue
        s, reason, gap = score(j)
        j.update(FitScore=s, FitReason=reason, GapNote=gap)
        kept.append(j)
        seen.add(j["url"])

    kept.sort(key=lambda x: -x["FitScore"])

    rows = [{
        "DateSeen": today, "Company": j["company"], "Title": j["title"],
        "Location": j["location"], "MinYOE": j.get("min_yoe", ""),
        "URL": j["url"], "Posted": j.get("posted", ""),
        "FitScore": j["FitScore"], "FitReason": j["FitReason"],
        "GapNote": j["GapNote"], "Status": "New",
        "Referral": "yes" if j.get("referral") else "",
        "AppliedDate": "", "FollowUpDate": "",
    } for j in kept]

    digest = build_digest(kept, len(raw), errors, today, max_age, too_old, undated)
    print(f"\n{len(kept)} new roles ({len(raw)} raw, {too_old} older than "
          f"{max_age}d, {undated} undated).")

    if dry_run:
        print("\n--- DRY RUN ---\n")
        print(digest)
        return

    append_pipeline(rows)
    save_seen(seen)
    if not kept:
        # On a short polling interval most runs find nothing. Saving seen.json
        # still matters; mailing an empty digest every couple of hours does not.
        print("No new roles; skipping email.")
        return
    send_email(f"Job Pipeline - {today} ({len(kept)} new)", digest)


def _age_label(j):
    a = j.get("age")
    if a is None:
        return "new since last scan"
    if a == 0:
        return "posted today"
    if a == 1:
        return "posted yesterday"
    return f"posted {a}d ago"


def build_digest(jobs, raw_count, errors, today, max_age, too_old, undated):
    agency = [j for j in jobs if j.get("agency")]
    direct = [j for j in jobs if not j.get("agency")]
    described = [j for j in direct if j.get("description")]
    titleonly = [j for j in direct if not j.get("description")]
    strong = [j for j in described if j["FitScore"] >= 70]
    mid = [j for j in described if 40 <= j["FitScore"] < 70]
    weak = [j for j in described if j["FitScore"] < 40]

    refs = [j for j in jobs if j.get("referral")]
    lines = [f"JOB PIPELINE - {today}",
             f"Roles posted in the last {max_age} day(s)"]
    if refs:
        lines.append(f"{len(refs)} at companies where you have a referral "
                     f"(marked *REFERRAL*)")
    lines += ["=" * 52, ""]

    lines.append(f"SECTION A - STRONG FIT ({len(strong)})")
    lines.append("-" * 52)
    if strong:
        for j in strong:
            lines += [
                f"[{j['FitScore']}]{' *REFERRAL*' if j.get('referral') else ''} {j['title']}",
                f"      {j['company']} | {j['location']} | {_age_label(j)}",
                f"      {j['FitReason']}",
                f"      {j['GapNote']}",
                f"      {j['url']}",
                "",
            ]
    else:
        lines += ["  (none today)", ""]

    lines.append(f"SECTION B - WORTH A LOOK ({len(mid)})")
    lines.append("-" * 52)
    for j in mid:
        tag = " *REFERRAL*" if j.get("referral") else ""
        lines.append(f"[{j['FitScore']}]{tag} {j['title']} - {j['company']}, "
                     f"{j['location']} ({_age_label(j)})\n      {j['url']}")
    if not mid:
        lines.append("  (none)")
    lines.append("")

    lines.append(f"SECTION C - LOW FIT: {len(weak)} roles filtered out")
    lines.append("")

    lines.append(f"SECTION D - TITLE MATCH ONLY ({len(titleonly)})")
    lines.append("-" * 52)
    lines.append("  Careers pages with no job description text. Not scored on")
    lines.append("  content - open the page to judge.")
    for j in titleonly:
        lines.append(f"  {j['title']} - {j['company']}, {j['location']}"
                     f"\n      {j['url']}")
    if not titleonly:
        lines.append("  (none)")

    lines.append("")
    lines.append(f"SECTION E - AGENCY / CONSULTANCY ({len(agency)})")
    lines.append("-" * 52)
    lines.append("  Client company NOT named. Before applying, search the JD")
    lines.append("  text to identify the employer, then check whether they")
    lines.append("  have a direct board above. Applying through an agency to")
    lines.append("  a company you later apply to directly can disqualify you.")
    for j in agency:
        lines.append(f"  {j['title']} - via {j['company']}, {j['location']}"
                     f"\n      {j['url']}")
    if not agency:
        lines.append("  (none)")

    lines += [
        "",
        "=" * 52,
        f"Raw roles polled: {raw_count}",
        f"New after filters: {len(jobs)}",
        f"Skipped as older than {max_age} days: {too_old}",
        f"No posting date from board: {undated} (kept, dated by first sighting)",
        f"Fetch errors: {errors} (see fetch_errors.log)",
    ]
    return "\n".join(lines)


def send_email(subject, body):
    user = os.environ.get("SMTP_USER")
    pw = os.environ.get("SMTP_PASS")
    to = os.environ.get("SMTP_TO", user)
    if not user or not pw:
        print("SMTP_USER/SMTP_PASS not set; printing digest instead.\n")
        print(body)
        return
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, pw)
        s.send_message(msg)
    print(f"Digest emailed to {to}")


def diagnose():
    """Poll every board and report WHY each failure happened. Distinguishes
    403 (blocked by IP or fingerprint) from 404 (dead slug) from timeouts."""
    from collections import Counter
    companies = load_companies()
    print(f"Diagnosing {len(companies)} boards...\n")
    buckets, dead = Counter(), []

    def probe(c):
        fn = ADAPTERS.get(c.get("ATS", "").lower())
        if not fn:
            return c, "unknown-ats", 0
        try:
            jobs = fn(c["Token"], c.get("Tenant"))
            return c, "ok", len(jobs)
        except Exception as e:
            msg = str(e)
            for code in ("403", "404", "429", "500", "503"):
                if code in msg:
                    return c, f"http-{code}", 0
            if "Timeout" in type(e).__name__ or "timeout" in msg.lower():
                return c, "timeout", 0
            if "JSONDecode" in type(e).__name__:
                return c, "bad-json", 0
            return c, type(e).__name__, 0

    empty = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for c, status, n in pool.map(probe, companies):
            if status == "ok" and n == 0:
                status = "ok-but-empty"      # 200 with no jobs = broken parser
                empty.append((c.get("ATS"), c.get("Company"), c.get("Token")))
            buckets[status] += 1
            if status not in ("ok", "ok-but-empty"):
                dead.append((status, c.get("ATS"), c.get("Company"), c.get("Token")))

    print("RESULT BY STATUS")
    for k, v in buckets.most_common():
        print(f"  {k:14} {v}")

    by_ats = Counter(f"{a}:{st}" for st, a, _, _ in dead)
    print("\nFAILURES BY ATS")
    for k, v in by_ats.most_common(12):
        print(f"  {k:26} {v}")

    print("\nInterpretation:")
    print("  many http-403  -> blocked by IP or fingerprint, not your config")
    print("  many http-404  -> stale slugs, delete or fix those rows")
    print("  many timeout   -> raise TIMEOUT or lower WORKERS")
    print("  many bad-json   -> endpoint returned non-JSON; check headers")
    if empty:
        print(f"\nOK BUT ZERO JOBS ({len(empty)}) - endpoint answered, parser "
              f"found nothing. These look healthy but contribute nothing:")
        for a, name, tok in sorted(empty):
            print(f"  {a:14} {name[:28]:30} {tok[:44]}")

    print("\n  Run with --prune to comment out every http-404 row.")

    with open(ROOT / "dead_rows.txt", "w", encoding="utf-8") as f:
        for st, a, name, tok in sorted(dead):
            f.write(f"{st}\t{a}\t{name}\t{tok}\n")
    print(f"\n{len(dead)} failing rows written to dead_rows.txt")


def prune():
    """Comment out every row that returned 404 in the last diagnose run."""
    path = ROOT / "dead_rows.txt"
    if not path.exists():
        print("No dead_rows.txt. Run --diagnose first.")
        return
    dead = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) >= 4 and parts[0] == "http-404":
            dead.add((parts[1].strip(), parts[3].strip()))
    if not dead:
        print("No http-404 rows to prune.")
        return

    out, removed = [], 0
    for line in COMPANIES_CSV.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped or line.startswith("Company,"):
            out.append(line)
            continue
        cols = [c.strip() for c in line.split(",")]
        if len(cols) >= 3 and (cols[1], cols[2]) in dead:
            out.append("# DEAD-404 " + line)
            removed += 1
        else:
            out.append(line)
    COMPANIES_CSV.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Commented out {removed} dead rows in companies.csv")


def repair_seen():
    """Rebuild seen.json from pipeline.csv.

    seen.json is meant to record "already emailed to you". A bug in run() also
    wrote every role that was merely older than --max-age into it, so hundreds
    of open roles were marked read without ever being reported. pipeline.csv is
    the real record of what was sent, so rebuild from that.
    """
    if not PIPELINE_CSV.exists():
        print("No pipeline.csv; nothing to repair.")
        return
    with open(PIPELINE_CSV, newline="", encoding="utf-8") as f:
        reported = {r["URL"] for r in csv.DictReader(f) if r.get("URL")}
    before = len(load_seen())
    save_seen(reported)
    print(f"seen.json rebuilt from pipeline.csv: {before} -> {len(reported)} "
          f"({before - len(reported)} never-reported URLs released).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--max-age", type=int, default=MAX_AGE_DAYS,
                    help="only report roles posted within N days "
                         "(default 2; 0 = no age limit)")
    ap.add_argument("--backlog", action="store_true",
                    help="clear the backlog: report every open role you have "
                         "not been sent yet, up to 90 days old. Postings older "
                         "than that are mostly reqs the board never closed; "
                         "use --max-age 0 if you want those too.")
    ap.add_argument("--repair-seen", action="store_true",
                    help="rebuild seen.json from pipeline.csv, dropping URLs "
                         "that were marked read but never actually reported")
    ap.add_argument("--diagnose", action="store_true",
                    help="report why each board failed, then exit")
    ap.add_argument("--prune", action="store_true",
                    help="comment out http-404 rows found by the last --diagnose")
    a = ap.parse_args()
    if a.prune:
        prune()
    elif a.diagnose:
        diagnose()
    elif a.repair_seen:
        repair_seen()
    else:
        run(dry_run=a.dry_run, reset=a.reset,
            max_age=BACKLOG_MAX_AGE if a.backlog else a.max_age)
