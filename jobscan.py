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
# Boards fetched in parallel. 12 is polite; raise only if runs feel slow.
WORKERS = 12

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
]

TITLE_KEEP = [
    "backend", "back-end", "back end", "software engineer",
    "software developer", "platform engineer", "full stack", "fullstack",
    "full-stack", "ai engineer", "ml engineer", "machine learning engineer",
    "applied ai", "api engineer", "sde", "member of technical staff",
    "application engineer", "server engineer", "infrastructure engineer",
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
            "Accept": "application/json"}

    out, offset = [], 0
    while offset < 200:
        body = {"appliedFacets": {}, "limit": 20, "offset": offset,
                "searchText": "India"}
        r = requests.post(api, json=body, headers=hdrs, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        posts = data.get("jobPostings", [])
        if not posts:
            break
        for j in posts:
            path = j.get("externalPath", "")
            out.append({
                "title": j.get("title", ""),
                "location": j.get("locationsText", "") or "",
                "url": f"{host}/en-US/{token}{path}",
                "description": " ".join(j.get("bulletFields") or []),
                "posted": _relative_posted(j.get("postedOn", "")),
            })
        offset += 20
        if offset >= data.get("total", 0):
            break
    return out


def fetch_agency(token, tenant=None):
    """Recruitment consultancies and staffing firms. Same scrape as custom, but
    flagged: the hiring company is not named, so these cannot be scored or
    researched and must never be treated as employer postings."""
    jobs = fetch_custom(token, tenant)
    for j in jobs:
        j["agency"] = True
    return jobs


ADAPTERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workable": fetch_workable,
    "oracle": fetch_oracle,
    "custom": fetch_custom,
    "agency": fetch_agency,
    "workday": fetch_workday,
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
    if "remote" in loc and ("apac" in loc or "asia" in loc):
        return True
    return False


def is_non_india_only(location):
    loc = (location or "").lower()
    if _has_india_city(loc) or _word("india", loc):
        return False
    return any(hint in loc for hint in NON_INDIA_HINTS)


def title_ok(title):
    t = (title or "").lower()
    if CAMPUS_DRIVE.search(title or ""):
        return False
    if any(bad in t for bad in TITLE_DROP):
        return False
    return any(good in t for good in TITLE_KEEP)


def company_ok(company):
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
    new_file = not PIPELINE_CSV.exists()
    cols = ["DateSeen", "Company", "Title", "Location", "MinYOE", "URL",
            "Posted", "FitScore", "FitReason", "GapNote", "Status",
            "AppliedDate", "FollowUpDate"]
    with open(PIPELINE_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new_file:
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
            for j in jobs:
                j["company"] = c.get("Company") or c["Token"]
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
        if not company_ok(j["company"]):
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
        elif age > max_age:
            too_old += 1
            seen.add(j["url"])   # never re-evaluate a stale posting
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
    send_email(f"Job Pipeline - {today}", digest)


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

    lines = [f"JOB PIPELINE - {today}",
             f"Roles posted in the last {max_age} day(s)", "=" * 52, ""]

    lines.append(f"SECTION A - STRONG FIT ({len(strong)})")
    lines.append("-" * 52)
    if strong:
        for j in strong:
            lines += [
                f"[{j['FitScore']}] {j['title']}",
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
        lines.append(f"[{j['FitScore']}] {j['title']} - {j['company']}, "
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

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for c, status, n in pool.map(probe, companies):
            buckets[status] += 1
            if status != "ok":
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


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--max-age", type=int, default=MAX_AGE_DAYS,
                    help="only report roles posted within N days (default 2)")
    ap.add_argument("--diagnose", action="store_true",
                    help="report why each board failed, then exit")
    ap.add_argument("--prune", action="store_true",
                    help="comment out http-404 rows found by the last --diagnose")
    a = ap.parse_args()
    if a.prune:
        prune()
    elif a.diagnose:
        diagnose()
    else:
        run(dry_run=a.dry_run, reset=a.reset, max_age=a.max_age)
