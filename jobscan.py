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

# Your years of experience. A role is judged on whether the band it states
# includes this number - "1-3", "2-4" and "2+" all do - not on a fixed window.
MY_YOE = 2
# Roles asking up to MY_YOE + this are kept but marked "stretch". Anything
# beyond is dropped: at 2 years, a "4+ years" req is an ATS knockout.
YOE_STRETCH = 1
# JDs often read "Bachelor's + 7 years OR Master's + 4 years". The Master's /
# PhD alternatives are ignored unless you hold one.
HAS_MASTERS = False

CORE_SKILLS = {
    "java": 3, "spring boot": 3, "springboot": 3, "spring": 2, "spring security": 2,
    "spring data": 2, "hibernate": 2, "jpa": 2, "rest api": 2,
    "restful": 2, "microservice": 3, "micro-service": 3, "postgresql": 3,
    "postgres": 3, "redis": 2, "kafka": 3, "rabbitmq": 2, "docker": 2,
    "kubernetes": 2, "k8s": 2, "aws": 2, "github actions": 1, "ci/cd": 1,
    "ci-cd": 1, "junit": 1, "mockito": 1, "sql": 1, "linux": 1, "maven": 1,
    "gradle": 1,
}

SECONDARY_SKILLS = {
    "typescript": 2, "node.js": 2, "nodejs": 2, "nestjs": 2, "nest.js": 2,
    "express.js": 1, "expressjs": 1, "react": 2, "reactjs": 2, "react.js": 2,
    "python": 3, "fastapi": 3, "django": 2,
    "javascript": 1, "websocket": 1, "grpc": 1, "terraform": 1,
}

AI_SKILLS = {
    "rag": 3, "retrieval-augmented": 3, "retrieval augmented": 3,
    "pgvector": 3, "vector database": 2, "vector db": 2, "vector search": 2,
    "embedding": 2, "reranking": 3, "rerank": 2, "langchain": 2,
    "langgraph": 3, "llm": 2, "large language model": 2,
    "generative ai": 2, "genai": 2, "gen ai": 2,
    "tool calling": 3, "function calling": 3, "agentic": 2, "ai agent": 2,
    "semantic search": 2, "prompt engineering": 1, "openai": 1,
    "hybrid retrieval": 3,
}

ALL_SKILLS = {**CORE_SKILLS, **SECONDARY_SKILLS, **AI_SKILLS}
# Calibrated on 450 live India JDs (2026-09-25) with whole-word matching:
# median 5 points, top 5% at 22+. So a JD squarely in your stack saturates.
MAX_SKILL_POINTS = 24

# Spellings of one skill. Each group scores once, at its highest weight, so
# "PostgreSQL (Postgres)" is not two skills.
SKILL_ALIASES = {
    "postgres": "postgresql", "nodejs": "node.js", "nest.js": "nestjs",
    "retrieval augmented": "rag", "retrieval-augmented": "rag",
    "rerank": "reranking", "large language model": "llm",
    "springboot": "spring boot", "micro-service": "microservice",
    "k8s": "kubernetes", "ci-cd": "ci/cd", "expressjs": "express.js",
    "reactjs": "react", "react.js": "react", "vector db": "vector database",
    "genai": "generative ai", "gen ai": "generative ai",
    "function calling": "tool calling",
}

# Whole-word, optional plural. Substring matching credited "rag" for every
# JD containing "storage", "leverage" or "coverage", "aws" for "laws" and
# "llm" for "enrollment" - rag was the second most-credited skill overall.
SKILL_RE = {s: re.compile(r"(?<![a-z0-9])" + re.escape(s) + r"s?(?![a-z0-9])")
            for s in ALL_SKILLS}

# Common asks that are NOT in your profile, shown as the gap line. Remove an
# entry when you add that skill to the banks above.
GAP_SKILLS = {
    "go": r"\bgolang\b|(?<=[,/(])\s*go\b|\bgo(?=\s*[,/)])",
    "c++": r"c\+\+", "c#": r"(?<![a-z])c#", ".net": r"\.net\b|\bdotnet\b",
    "rust": r"\brust\b", "scala": r"\bscala\b", "ruby": r"\bruby\b|\brails\b",
    "php": r"\bphp\b", "angular": r"\bangular", "vue": r"\bvue(?:\.?js)?\b",
    "kotlin": r"\bkotlin\b", "gcp": r"\bgcp\b|google cloud",
    "azure": r"\bazure\b", "spark": r"\b(?:py)?spark\b", "airflow": r"\bairflow\b",
    "snowflake": r"\bsnowflake\b", "pytorch": r"\bpytorch\b",
    "tensorflow": r"\btensorflow\b", "mongodb": r"\bmongo(?:db)?\b",
    "elasticsearch": r"\belastic ?search\b", "graphql": r"\bgraphql\b",
    "embedded/C": r"\bembedded (?:c|systems|software|linux)\b|\brtos\b|\bfirmware\b",
    "salesforce": r"\bsalesforce\b|\bapex\b", "sap": r"\bsap\b|\babap\b",
    "networking": r"\b(?:bgp|ospf|mpls|l2/l3)\b",
}
GAP_RE = {k: re.compile(v) for k, v in GAP_SKILLS.items()}

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
    "engineer 3", "engineer iii", "associate engineer",
    "programmer", "sdet", "software eng", "engineer -", "engineer,",
    # AI and cloud titles the list above missed entirely
    "llm", "genai", "gen ai", "generative ai", "mlops", "ml ops", "ai/ml",
    "forward deployed", "product engineer", "founding engineer",
    "research engineer", "python engineer", "reliability engineer",
]

TITLE_DROP = [
    "staff", "principal", "lead", "leader", "manager", "director", "architect",
    "head of", "vp ", "vice president", "avp", "assistant vice president",
    "intern", "internship", "president", "chief", "fellow", "distinguished",
    "senior staff",
    # new-grad programmes: at 2 years you are outside their eligibility
    "graduate", "new grad", "trainee", "apprentice", "fresher", "freshers",
]

# Campus hiring drives: "IIT Jammu 2026 || TravClan || SDE-1" and similar.
CAMPUS_DRIVE = re.compile(r"\d{4}\s*\|\||\|\|\s*\d{4}|campus\s+(drive|hiring)", re.I)

# Level words that usually mean 4-6+ years. Not dropped outright - Indian
# product companies do post "Senior Software Engineer (2-4 years)" - but a
# senior-titled role is only kept when its JD states a band you fall in.
SENIOR_RE = re.compile(
    r"\b(?:senior|sr|snr)\b|\b(?:iii|iv)\b|\b(?:sde|engineer|developer)[\s-]*[34]\b"
    r"|\blevel\s*[3-9]\b", re.I)

# Graduation-year gates ("2025/2026 batch only") exclude anyone who
# graduated before them, whatever the years line says.
_THIS_YEAR = datetime.now(timezone.utc).year
_GATED_YEARS = "|".join(str(y) for y in range(_THIS_YEAR - 1, _THIS_YEAR + 3))
BATCH_GATE_RE = re.compile(
    rf"\b(?:{_GATED_YEARS})\s*(?:batch|graduates?|pass[- ]?outs?|passing out)\b"
    rf"|\bgraduat\w*\s+(?:in|by)\s+(?:{_GATED_YEARS})\b"
    rf"|\bclass of (?:{_GATED_YEARS})\b"
    rf"|\b(?:batch|pass(?:ed)?[- ]?out|graduation year)\s*[:\-]?\s*(?:{_GATED_YEARS})\b",
    re.I)

COMPANY_DROP = [
    "accenture", "tcs", "tata consultancy", "infosys", "wipro",
    "cognizant", "capgemini", "hcl", "tech mahindra", "ltimindtree",
    "mindtree", "mphasis", "birlasoft", "hexaware", "zensar",
    "randstad", "adecco", "manpower", "michael page", "robert half",
    "staffing", "recruitment", "consultancy services", "talent solutions",
]

# Years-of-experience mentions: "2-4 years", "3+ yrs", "7 + to 10 years",
# "2years'experience", "at least two years". Numbers only; context is judged
# in yoe_band().
_NUMWORD = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
            "fifteen": 15}
_N = r"(\d{1,2}(?:\.\d)?|" + "|".join(_NUMWORD) + r")"
YOE_RE = re.compile(
    rf"\b{_N}\s*(\+)?\s*(?:(?:-|–|—|to)\s*\+?\s*{_N}\s*)?\+?\s*"
    r"(?:years?|yrs?)(?![a-z])['’]?\s*\+?", re.I)
# "of relevant experience", "applied experience", "of full stack experience"
YOE_EXP_AFTER = re.compile(
    r"\s*(?:of\s+)?(?:[\w/.-]+\s+){0,6}?(?:experience|experinece|exp\b|expertise)", re.I)
YOE_EXP_BEFORE = re.compile(r"(?:experience|exp)\s*[:\-(]?\s*(?:of\s+)?"
                            r"(?:minimum\s+(?:of\s+)?|min\.?\s*)?$", re.I)
# "5+ years in ML systems", "3 years building APIs"
YOE_CONNECTOR = re.compile(r"\s*(?:of|in|on|as|with|working|building|developing|"
                           r"designing|writing|hands|delivering|leading)\b", re.I)
# company history, not a requirement: "for 40 years", "in the last 1 year"
YOE_BLURB = re.compile(r"\b(?:for|over|than|past|last|since|nearly|almost|founded|"
                       r"history)\s*(?:the\s+)?(?:last\s+|past\s+)?$", re.I)
YOE_PREFERRED = re.compile(
    r"\b(?:prefer(?:red|ably)?|nice[- ]to[- ]have|good[- ]to[- ]have|a plus|"
    r"bonus|ideally|desir(?:ed|able)|advantage(?:ous)?)\b", re.I)
# These mark the whole line optional wherever they sit ("2+ years of Go is a
# plus"); the softer words above only do when they come before the years
# ("Preferred: 5+ years"), not after ("3+ years, preferably in fintech").
YOE_OPTIONAL_LINE = re.compile(
    r"\b(?:a plus|nice[- ]to[- ]have|good[- ]to[- ]have|bonus|advantageous)\b", re.I)
YOE_REQUIRED_HDR = re.compile(
    r"\b(?:minimum|basic|required|requirements?|must|qualifications?|what you|"
    r"who you|you have|you bring|about you|responsibilit)", re.I)
YOE_ADV_DEGREE = re.compile(
    r"\b(?:master'?s?|master’s|m\.?s\.?|m\.?tech|mba|ph\.?\s?d|doctorate|"
    r"advanced degree|post[- ]?graduate)\b", re.I)
YOE_DEGREE = re.compile(r"\b(?:bachelor|b\.?\s?tech|b\.?\s?e\b|b\.?s\b|degree)", re.I)
YOE_BACHELOR = re.compile(r"\b(?:bachelor|b\.?\s?tech|b\.?\s?e\b|b\.?\s?s\b|undergraduate)", re.I)
YOE_DEGREE_START = re.compile(
    r"\s*(?:an?\s+)?(?:bachelor|master|ph\.?\s?d|b\.?\s?tech|m\.?\s?tech|b\.?\s?e\b|"
    r"m\.?\s?s\b|mba|doctorate|advanced)", re.I)
# Split "A or B" only where B is a real alternative route - a years figure or
# a degree. "Bachelor's in CS or related field and 5+ years" is one route.
YOE_ALT_SPLIT = re.compile(
    r";\s*\bor\b\s*|,?\s+\bor\b\s+(?=(?:at\s+least\s+|minimum\s+(?:of\s+)?|an?\s+)?"
    r"(?:\d|" + "|".join(_NUMWORD) + r"\b|bachelor|master|ph\.?\s?d|b\.?\s?tech|"
    r"m\.?\s?tech|b\.?\s?e\b|m\.?\s?s\b|mba|doctorate|advanced))", re.I)
# Walmart-style "Option 1: Bachelor's and 2 years. Option 2: 4 years." -
# option 2 onward are the no-degree routes.
YOE_OPTION = re.compile(r"\boption\s*([1-9])\s*[:\-]", re.I)

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
                # Not the API ref with the host swapped: that keeps a /postings/
                # segment, and jobs.smartrecruiters.com/<co>/postings/<id> 404s.
                "url": f"https://jobs.smartrecruiters.com/{token}/{j.get('id')}",
                "description": "",  # filled by enrich() for roles that pass filters
                "posted": (j.get("releasedDate") or "")[:10],
                "_detail": ("smartrecruiters", j.get("ref") or
                            f"https://api.smartrecruiters.com/v1/companies/{token}"
                            f"/postings/{j.get('id')}"),
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
                # a one-line summary; enrich() swaps in the full JD
                "description": strip_html(j.get("ShortDescriptionStr", "")),
                "posted": (j.get("PostedDate") or "")[:10],
                "_detail": ("oracle",
                            f"https://{tenant}/hcmRestApi/resources/latest/"
                            f"recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
                            f"&finder=ById;Id=%22{rid}%22,siteNumber={site}"),
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
        # experience reads like "2 - 4 Years"; append it so yoe_band() can see it
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
                # bulletFields is a requisition id, not a description. Passing
                # it off as one is what scored every Workday role on its title
                # alone and buried them in Section C. enrich() fetches the JD.
                "description": "",
                "posted": _relative_posted(j.get("postedOn", "")),
                "_detail": ("workday", f"{api[:-len('/jobs')]}{path}"),
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
            # basic_qualifications carries the YOE line yoe_band() looks for
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
            detail = f"{base}/{j.get('id')}" + (f"?domain={dom}" if dom else "")
            out.append({
                "title": j.get("name", ""),
                "location": "; ".join(dict.fromkeys(str(x) for x in locs)),
                "url": url_j,
                # the list endpoint returns job_description empty; enrich() fills it
                "description": strip_html(j.get("job_description") or ""),
                "posted": posted,
                "_detail": ("eightfold", detail),
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
# JD ENRICHMENT - one detail call per role that survives the cheap filters
# --------------------------------------------------------------------------
# Workday, SmartRecruiters, Oracle and Eightfold list endpoints return a title
# and a location but no description. Scoring those on the title alone put
# 1,140 of 1,257 Workday roles - most of them at referral companies - into the
# Section C head count, never shown. Each board has a public per-job endpoint;
# call it only for roles that are new, in India, in range and on-title, so a
# 2-hourly scan costs a few dozen requests and a backlog run a few hundred.

# Below this many characters a "description" is a summary line or nothing:
# not enough to read years or stack from, so the role is routed as title-only.
JD_MIN_CHARS = 200
ENRICH_MAX = 2000
ENRICH_WORKERS = 8


def has_jd(job):
    return len(job.get("description") or "") >= JD_MIN_CHARS


def enrich(job):
    """Fetch the full JD in place. Returns True on success. Never raises: a
    failed detail call leaves the list-level data, and the role is shown as
    title-only rather than scored on nothing."""
    kind, url = job.get("_detail") or (None, None)
    if not kind:
        return False
    try:
        data = get_json(url, retries=1)
        if kind == "workday":
            info = data.get("jobPostingInfo") or {}
            desc = strip_html(info.get("jobDescription") or "")
            locs = [info.get("location")] + list(info.get("additionalLocations") or [])
            locs = [str(x) for x in locs if x]
            if locs:
                job["location"] = "; ".join(dict.fromkeys(locs))
            if info.get("startDate"):
                job["posted"] = str(info["startDate"])[:10]
        elif kind == "smartrecruiters":
            secs = (data.get("jobAd") or {}).get("sections") or {}
            desc = strip_html(" ".join(
                (secs.get(k) or {}).get("text") or ""
                for k in ("jobDescription", "qualifications", "additionalInformation")))
        elif kind == "oracle":
            it = (data.get("items") or [{}])[0]
            desc = strip_html(" ".join(
                it.get(k) or "" for k in ("ExternalDescriptionStr",
                                          "ExternalResponsibilitiesStr",
                                          "ExternalQualificationsStr")))
        elif kind == "eightfold":
            desc = strip_html(data.get("job_description") or "")
        else:
            return False
    except Exception as e:
        job["_enrich_error"] = f"{type(e).__name__}: {e}"[:160]
        return False
    if len(desc) > len(job.get("description") or ""):
        job["description"] = desc
    return True


def _interleave_by_company(jobs):
    """Round-robin across companies, so eight workers are not all hitting one
    Workday tenant at once - that is how a board starts returning 429s."""
    groups = {}
    for j in jobs:
        groups.setdefault(j.get("company", ""), []).append(j)
    out, queues = [], list(groups.values())
    while queues:
        queues = [q for q in queues if q]
        for q in queues:
            out.append(q.pop(0))
    return out


def enrich_all(jobs):
    """Enrich every thin-JD role that has a detail endpoint. Returns
    (attempted, failed)."""
    todo = [j for j in jobs if j.get("_detail") and not has_jd(j)]
    todo = _interleave_by_company(todo)[:ENRICH_MAX]
    if not todo:
        return 0, 0
    print(f"Fetching {len(todo)} job descriptions...")
    with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as pool:
        ok = list(pool.map(enrich, todo))
    failed = ok.count(False)
    if failed:
        by_co = {}
        for j, good in zip(todo, ok):
            if not good:
                by_co.setdefault(j.get("company", "?"), j.get("_enrich_error", ""))
        for co, err in by_co.items():
            log_error(co, f"JD fetch failed ({err}); shown as title-only")
    return len(todo), failed

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


def is_senior_title(title):
    return bool(SENIOR_RE.search(title or ""))


def company_ok(company, referral=False):
    """COMPANY_DROP exists to keep mass-market service firms out of the digest.
    A referral changes that calculus entirely - a role you can be walked into
    is worth seeing whoever posted it - so a Referral row skips the check.
    """
    if referral:
        return True
    c = (company or "").lower()
    return not any(bad in c for bad in COMPANY_DROP)


def _yoe_num(s):
    if s is None:
        return None
    s = s.lower()
    return float(_NUMWORD[s]) if s in _NUMWORD else float(s)


def _yoe_mentions(text, is_title=False):
    """(lo, hi) for each years-of-experience mention that reads as a
    requirement. hi is None for open-ended ("3+ years")."""
    out = []
    for m in YOE_RE.finditer(text):
        lo, hi = _yoe_num(m.group(1)), _yoe_num(m.group(3))
        # nobody asks a junior for 20+ years; a number that size is company
        # history ("innovating for 40 years") or a typo
        if lo is None or lo > 20 or (hi is not None and (hi > 25 or hi < lo)):
            continue
        if not is_title:
            before = text[max(0, m.start() - 40):m.start()]
            after = text[m.end():m.end() + 80]
            strong = YOE_EXP_AFTER.match(after) or YOE_EXP_BEFORE.search(before)
            if not strong and (YOE_BLURB.search(before)
                               or not YOE_CONNECTOR.match(after)):
                continue
        out.append((lo, hi))
    return out


def _line_requirement(line):
    """Requirement stated by one line, resolving "A or B" alternatives the
    way a Bachelor's holder reads them. None if the line gates nothing."""
    clauses = []
    for part in YOE_ALT_SPLIT.split(line):
        if not part or not part.strip():
            continue
        # "Bachelor's or Master's degree with 6-8 years" / "BE or BTech with
        # 3+ years": a degree list, one route. Re-join it.
        if clauses and not _yoe_mentions(clauses[-1]) and YOE_DEGREE_START.match(part):
            clauses[-1] += " or " + part
        else:
            clauses.append(part)
    numbered, degree_only = [], False     # numbered: (lo, hi, names_a_degree)
    for c in clauses:
        if not HAS_MASTERS and YOE_ADV_DEGREE.search(c) and not YOE_BACHELOR.search(c):
            continue                      # a Master's/PhD-only route
        ms = _yoe_mentions(c)
        degree = bool(YOE_DEGREE.search(c))
        if not ms:
            degree_only = degree_only or degree
            continue
        # conjunctive inside one clause: "5+ years overall, 2+ in Go" is 5
        lo, hi = max(ms, key=lambda x: (x[0], x[1] is not None))
        numbered.append((lo, hi, degree))
    if not numbered:
        return None
    if len(numbered) == 1 and not degree_only:
        return numbered[0][:2]
    with_degree = [n for n in numbered if n[2]]
    if with_degree:
        pool = with_degree
    elif degree_only:
        # "Bachelor's degree, OR 3+ years of work experience": the degree
        # route alone satisfies the line, so the years figure gates nothing
        return None
    else:
        pool = numbered
    # alternatives: any one route suffices, so the easiest one counts
    return min(pool, key=lambda r: r[0])[:2]


def yoe_band(title, description=""):
    """(min, max) years the role asks for; max is None when open-ended.
    Returns None when nothing parseable is stated.

    A years figure in the title wins outright: Cisco and Visa put the real
    band there ("Software Engineer (1 - 2 years ...)") while the body carries
    a generic template. Otherwise the requirement is the highest minimum
    across required lines - "3+ years of software development, 2+ years of
    design" asks for 3, not 2, which is what the old min() got wrong.
    Preferred/nice-to-have lines and Master's/PhD routes are ignored.
    """
    t = _yoe_mentions(title or "", is_title=True)
    if t:
        return max(t, key=lambda x: (x[0], x[1] is not None))
    text = (description or "")[:10000]
    if not text:
        return None
    text = YOE_OPTION.sub(lambda m: "\n" + ("" if m.group(1) == "1" else "@ALT@ "), text)
    best, preferred = None, False
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("@ALT@"):
            continue
        header = (not re.search(r"\d", line)
                  and (len(line.split()) <= 5 or (len(line) < 70 and line.endswith(":"))))
        if header and YOE_PREFERRED.search(line):
            preferred = True            # a "Preferred Qualifications" header
            continue
        if header and YOE_REQUIRED_HDR.search(line):
            preferred = False
            continue
        if preferred or YOE_OPTIONAL_LINE.search(line):
            continue
        first = YOE_RE.search(line)
        soft = YOE_PREFERRED.search(line)
        if first and soft and soft.start() < first.start():
            continue
        req = _line_requirement(line)
        if req and (best is None or (req[0], req[1] is not None) > (best[0], best[1] is not None)):
            best = req
    return best


def band_fit(band):
    """in-band / stretch / below / over / unstated, relative to MY_YOE."""
    if band is None:
        return "unstated"
    lo, hi = band
    if lo > MY_YOE + YOE_STRETCH:
        return "over"
    if lo > MY_YOE:
        return "stretch"
    if hi is not None and hi < MY_YOE:
        return "below"          # "0-1 years": you are past the band
    return "in-band"


def band_label(band):
    if band is None:
        return "years not stated"
    lo, hi = band
    f = lambda x: str(int(x)) if x == int(x) else str(x)
    return f"{f(lo)}-{f(hi)} yrs" if hi is not None else f"{f(lo)}+ yrs"


def skill_hits(blob):
    """Canonical skills found, highest weight first."""
    best = {}
    for skill, weight in ALL_SKILLS.items():
        if SKILL_RE[skill].search(blob):
            canon = SKILL_ALIASES.get(skill, skill)
            best[canon] = max(best.get(canon, 0), weight)
    return sorted(best.items(), key=lambda kv: -kv[1])


def gap_skills(blob, limit=4):
    counts = []
    for label, rx in GAP_RE.items():
        if label in ALL_SKILLS:
            continue
        n = len(rx.findall(blob))
        if n:
            counts.append((n, label))
    return [label for _, label in sorted(counts, key=lambda x: -x[0])[:limit]]


def role_points(title):
    t = (title or "").lower()
    if any(k in t for k in ["backend", "back-end", "back end"]):
        return 20
    if any(k in t for k in ["ai engineer", "ml engineer", "applied ai", "machine learning",
                            "llm", "genai", "gen ai", "generative ai", "ai/ml",
                            "forward deployed"]):
        return 20
    if any(k in t for k in ["full stack", "fullstack", "full-stack", "platform", "cloud",
                            "devops", "site reliability", "sre", "mlops", "ml ops",
                            "infrastructure"]):
        return 16
    if any(k in t for k in ["software engineer", "software developer", "sde",
                            "product engineer", "member of technical staff"]):
        return 14
    if "data engineer" in t:
        return 10
    return 8


YOE_POINTS = {"in-band": 30, "unstated": 18, "stretch": 12, "below": 15}


def score(job):
    """Adds FitScore / Skills / Gaps / FitReason / GapNote to the job and
    returns the score. 50% weighted skill overlap, 30% years fit, 20% role."""
    blob = f"{job['title']} {job['description']}".lower()
    hits = skill_hits(blob)
    points = sum(w for _, w in hits)
    skill_score = min(points / MAX_SKILL_POINTS, 1.0) * 50
    fit = job.get("yoe_fit", "unstated")
    total = round(skill_score + YOE_POINTS.get(fit, 0) + role_points(job["title"]))

    job["Skills"] = [s for s, _ in hits]
    job["Gaps"] = gap_skills(blob) if has_jd(job) else []
    top = job["Skills"][:3]
    job["FitReason"] = ("Overlap: " + ", ".join(top)) if top else "No direct stack overlap detected"
    band = band_label(job.get("yoe_band"))
    if not has_jd(job):
        job["GapNote"] = "No JD text - open the posting to judge"
    elif job["Gaps"]:
        job["GapNote"] = f"{band} ({fit}); JD also wants: {', '.join(job['Gaps'])}"
    else:
        job["GapNote"] = f"{band} ({fit}); no blocking gap detected"
    job["FitScore"] = min(total, 100)
    return job["FitScore"]


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


# Old SmartRecruiters links carried the API's /postings/ segment and 404ed.
SR_LEGACY = re.compile(r"(https://jobs\.smartrecruiters\.com/[^/\s]+)/postings/(\d+)")


def canon_url(url):
    """Map a legacy SmartRecruiters link onto the working form, so seen.json
    and pipeline.csv keep matching it after the adapter fix."""
    return SR_LEGACY.sub(r"\1/\2", url or "")


def load_seen():
    if SEEN_JSON.exists():
        return {canon_url(u) for u in json.loads(SEEN_JSON.read_text())}
    return set()


def save_seen(seen):
    SEEN_JSON.write_text(json.dumps(sorted(seen), indent=0))


PIPELINE_COLS = ["DateSeen", "Company", "Title", "Location", "MinYOE", "YOEBand",
                 "YOEFit", "Referral", "URL", "Posted", "FitScore", "FitReason",
                 "GapNote", "Section", "Status", "AppliedDate", "FollowUpDate"]
# Yours. A role that is rescored keeps whatever you put in these.
USER_COLS = ("Status", "AppliedDate", "FollowUpDate")


def read_pipeline():
    if not PIPELINE_CSV.exists():
        return [], list(PIPELINE_COLS)
    with open(PIPELINE_CSV, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        rows = list(rd)
        extra = [c for c in (rd.fieldnames or []) if c not in PIPELINE_COLS]
    return rows, PIPELINE_COLS + extra


def upsert_pipeline(rows, drops=(), user=False):
    """One row per URL. New roles are appended; a role evaluated again (a
    backlog re-check) is updated in place, keeping the original DateSeen and
    anything in USER_COLS - unless `user` is set, i.e. the write is you
    recording an application. `drops` are (url, reason) pairs: roles already
    in the file that were re-checked with their full JD and filtered out."""
    old, cols = read_pipeline()
    index = {}
    for r in old:
        r["URL"] = canon_url(r.get("URL"))
        if r["URL"] in index:
            continue                    # keep the first sighting
        index[r["URL"]] = r
    for r in rows:
        prev = index.get(r["URL"])
        if prev is None:
            index[r["URL"]] = dict(r)
            continue
        for k, v in r.items():
            if k == "DateSeen" and prev.get(k):
                continue
            if k in USER_COLS and prev.get(k) and not user:
                continue
            prev[k] = v
    for url, reason in drops:
        if url in index:
            index[url]["Section"] = "X"
            index[url]["GapNote"] = f"Rechecked with full JD: {reason}"
    with open(PIPELINE_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in index.values():
            w.writerow({c: (r.get(c) if r.get(c) is not None else "") for c in cols})


# --------------------------------------------------------------------------
# APPLICATIONS - applied.csv is the one file you write; the scanner reads it
# --------------------------------------------------------------------------
# Record an application from your phone: Actions > jobscan > Run workflow >
# mode "applied", paste the URL. Or edit applied.csv directly.

APPLIED_CSV = ROOT / "applied.csv"
APPLIED_COLS = ["Date", "Company", "Title", "URL", "Via", "Status", "Notes"]
APPLIED_STATUSES = ("applied", "referred", "interview", "rejected", "offer", "withdrawn")
# Nudge to follow up between these many days after applying
FOLLOWUP_WINDOW = (7, 21)
# Applied somewhere this recently? New roles there are flagged, and demoted in
# APPLY FIRST: one well-aimed application per company beats five.
COMPANY_COOLDOWN_DAYS = 30


def load_applied():
    if not APPLIED_CSV.exists():
        return []
    with open(APPLIED_CSV, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if (r.get("URL") or r.get("Company"))]


def _days_since(date_str):
    try:
        d = datetime.strptime((date_str or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return (datetime.now(IST).date() - d).days


def mark_applied(url, status="applied", note=""):
    """Record an application in applied.csv and pipeline.csv."""
    url = canon_url((url or "").strip())
    status = (status or "applied").strip().lower()
    if not url:
        sys.exit("--applied needs the posting URL")
    if status not in APPLIED_STATUSES:
        sys.exit(f"status must be one of {', '.join(APPLIED_STATUSES)}")
    today = datetime.now(IST).strftime("%Y-%m-%d")
    pipe = {canon_url(r.get("URL")): r for r in read_pipeline()[0]}
    p = pipe.get(url, {})
    rows = load_applied()
    for r in rows:
        if canon_url(r.get("URL")) == url:
            r["Status"] = status
            if note:
                r["Notes"] = note
            break
    else:
        rows.append({"Date": today, "Company": p.get("Company", ""),
                     "Title": p.get("Title", ""), "URL": url,
                     "Via": "referral" if status == "referred" else "direct",
                     "Status": status, "Notes": note})
    with open(APPLIED_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=APPLIED_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    if p:
        upsert_pipeline([{"URL": url, "Status": status.title(),
                          "AppliedDate": p.get("AppliedDate") or today}], user=True)
    who = f"{p.get('Title')} at {p.get('Company')}" if p else url
    print(f"Recorded: {who} -> {status}"
          + ("" if p else " (not in pipeline.csv; fill Company/Title in applied.csv)"))


def recent_by_company(applied):
    out = {}
    for r in applied:
        d = _days_since(r.get("Date"))
        co = (r.get("Company") or "").strip().lower()
        if co and d is not None and d <= COMPANY_COOLDOWN_DAYS:
            out.setdefault(co, []).append((d, r.get("Title") or "a role"))
    return out


def followups_due(applied):
    lo, hi = FOLLOWUP_WINDOW
    due = []
    for r in applied:
        d = _days_since(r.get("Date"))
        if (r.get("Status") or "").lower() in ("applied", "referred") and d is not None \
                and lo <= d <= hi:
            due.append((d, r))
    return sorted(due, key=lambda x: -x[0])


def _followups_hour():
    """Follow-ups ride on one scan a day (the 02:00 UTC / 07:30 IST cron)
    rather than all twelve, and on every manual run. Hour 3 is included
    because scheduled runs often start late; runs are two hours apart, so
    only one of them lands in the window."""
    if os.environ.get("MODE", "scan") != "scan":
        return True
    hours = os.environ.get("FOLLOWUP_HOURS_UTC", "2,3")
    return str(datetime.now(timezone.utc).hour) in [h.strip() for h in hours.split(",")]


# --------------------------------------------------------------------------
# RUN
# --------------------------------------------------------------------------

# Workday lists a role open in many cities as "51 Locations"; the detail call
# names them, so keep these for enrichment and judge location afterwards.
MULTI_LOC = re.compile(r"^\d+\s+locations?$", re.I)
ERRORS_LOG_KEEP = 5000


def _trim_errors_log():
    """fetch_errors.log is committed every run; keep it from growing forever."""
    if not ERRORS_LOG.exists():
        return
    lines = ERRORS_LOG.read_text(encoding="utf-8").splitlines(keepends=True)
    if len(lines) > ERRORS_LOG_KEEP:
        ERRORS_LOG.write_text("".join(lines[-ERRORS_LOG_KEEP:]), encoding="utf-8")


def poll_boards(companies):
    raw, errors = [], 0

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
    return raw, errors


def select(raw, seen, max_age):
    """Cheap filters, then one detail call per survivor, then the filters
    that need the JD. Returns (kept, drops, stats); drops are (url, reason)."""
    from collections import Counter
    stats = Counter()
    stage = {}
    for j in raw:
        j["url"] = canon_url(j.get("url"))
        if not j["url"] or j["url"] in seen or j["url"] in stage:
            continue
        if not company_ok(j["company"], j.get("referral")):
            continue
        if not title_ok(j["title"]):
            continue
        loc = (j.get("location") or "").strip()
        multi = bool(MULTI_LOC.match(loc)) and j.get("_detail")
        if not multi and (is_non_india_only(loc) or not is_india(loc)):
            continue
        age = age_days(j.get("posted"))
        if age is not None and max_age > 0 and age > max_age:
            # NOT added to seen: an old posting is still an open posting, and
            # marking it read here is what silently hid 500+ live roles.
            stats["too_old"] += 1
            continue
        stage[j["url"]] = j

    attempted, failed = enrich_all(list(stage.values()))
    stats["enriched"], stats["enrich_failed"] = attempted - failed, failed

    kept, drops = [], []
    for j in stage.values():
        loc = j.get("location") or ""
        if is_non_india_only(loc) or not is_india(loc):
            continue                    # the detail call named non-India cities
        age = age_days(j.get("posted"))
        if age is not None and max_age > 0 and age > max_age:
            stats["too_old"] += 1       # the detail call gave the real date
            continue
        j["age"] = age
        text = f"{j['title']}\n{j.get('description') or ''}"
        if BATCH_GATE_RE.search(text):
            stats["batch_gated"] += 1
            drops.append((j["url"], "graduation-year gated"))
            continue
        band = yoe_band(j["title"], j.get("description") or "")
        fit = band_fit(band)
        if fit == "over":
            stats["over_yoe"] += 1
            drops.append((j["url"], f"asks {band_label(band)}"))
            continue
        if is_senior_title(j["title"]) and fit not in ("in-band", "stretch"):
            # "Senior"/"III" with no stated band you fall in reads as 4-6+ yrs
            stats["senior_unproven"] += 1
            drops.append((j["url"], f"senior title, {band_label(band)}"))
            continue
        if age is None:
            stats["undated"] += 1
        j["yoe_band"], j["yoe_fit"] = band, fit
        score(j)
        kept.append(j)
    return kept, drops, stats


def run(dry_run=False, reset=False, max_age=MAX_AGE_DAYS):
    _trim_errors_log()
    companies = load_companies()
    seen = set() if reset else load_seen()
    applied = load_applied()
    today = datetime.now(IST).strftime("%Y-%m-%d")

    print(f"Polling {len(companies)} boards with {WORKERS} workers...")
    raw, errors = poll_boards(companies)
    kept, drops, stats = select(raw, seen, max_age)

    recent = recent_by_company(applied)
    for j in kept:
        hist = recent.get(j["company"].strip().lower())
        if hist:
            d, title = min(hist)
            j["applied_recently"] = f"you applied for '{title}' here {d}d ago"
    followups = followups_due(applied) if _followups_hour() else []

    digest, sections = build_digest(kept, stats, len(raw), errors, today, max_age, followups)
    print(f"\n{len(kept)} new roles ({len(raw)} raw, {stats['too_old']} older than "
          f"{max_age}d, {stats['over_yoe']} over your years, "
          f"{stats['senior_unproven']} senior without a band you fit).")

    if dry_run:
        print("\n--- DRY RUN ---\n")
        print(digest)
        return

    rows = [{
        "DateSeen": today, "Company": j["company"], "Title": j["title"],
        "Location": j["location"],
        "MinYOE": "" if not j.get("yoe_band") else f"{j['yoe_band'][0]:g}",
        "YOEBand": band_label(j.get("yoe_band")) if j.get("yoe_band") else "",
        "YOEFit": j.get("yoe_fit", ""),
        "URL": j["url"], "Posted": j.get("posted", ""),
        "FitScore": j["FitScore"], "FitReason": j["FitReason"],
        "GapNote": j["GapNote"], "Section": j.get("Section", ""), "Status": "New",
        "Referral": "yes" if j.get("referral") else "",
        "AppliedDate": "", "FollowUpDate": "",
    } for j in kept]
    upsert_pipeline(rows, drops)
    seen.update(j["url"] for j in kept)
    save_seen(seen)
    if not kept and not followups:
        # On a short polling interval most runs find nothing. Saving seen.json
        # still matters; mailing an empty digest every couple of hours does not.
        print("No new roles; skipping email.")
        return
    first, refs = len(sections["first"]), sum(1 for j in kept if j.get("referral"))
    subject = f"Jobs {today}: {first} to apply first, {len(kept)} new"
    if refs:
        subject += f", {refs} at referral companies"
    if not kept:
        subject = f"Jobs {today}: {len(followups)} follow-ups due"
    send_email(subject, digest)


# --------------------------------------------------------------------------
# DIGEST
# --------------------------------------------------------------------------

APPLY_FIRST_MAX = 8
APPLY_FIRST_MIN_SCORE = 55
PER_COMPANY_CAP = 2
# A referral is the biggest single lever on getting shortlisted, so a
# referral role outranks a slightly better text match without one.
REFERRAL_BONUS = 12


def priority(j):
    age = j.get("age")
    fresh = {0: 6, 1: 4, 2: 2}.get(age, 0) if age is not None else 0
    return (j["FitScore"] + (REFERRAL_BONUS if j.get("referral") else 0) + fresh
            - (8 if j.get("applied_recently") else 0))


def _age_label(j):
    a = j.get("age")
    if a is None:
        return "new since last scan"
    if a == 0:
        return "posted today"
    if a == 1:
        return "posted yesterday"
    return f"posted {a}d ago"


def _dedupe_postings(jobs):
    """Workday posts one req per city with an identical JD. Show it once and
    list the other cities, so you apply once rather than five times."""
    out, by_key = [], {}
    for j in jobs:
        if not has_jd(j):
            out.append(j)
            continue
        key = (j["company"].lower(), j["title"].strip().lower(),
               re.sub(r"\s+", " ", j["description"][:600]))
        if key in by_key:
            by_key[key].setdefault("also", []).append(j)
            continue
        by_key[key] = j
        out.append(j)
    return out


def _action(j):
    if j.get("referral"):
        return (f"Referral company: send this link to your {j['company']} referrer and "
                f"let them submit you BEFORE you apply - an existing application "
                f"usually blocks or voids the referral.")
    if j.get("yoe_fit") == "stretch":
        return (f"Stretch ({band_label(j.get('yoe_band'))}): apply if your work maps "
                f"closely, and lead with shipped scope, not tenure.")
    return "Apply directly - today if you can; the first 48h get the most recruiter attention."


def build_digest(jobs, stats, raw_count, errors, today, max_age, followups=()):
    agency = [j for j in jobs if j.get("agency")]
    direct = [j for j in jobs if not j.get("agency")]
    titleonly = [j for j in direct if not has_jd(j)]
    described = _dedupe_postings(
        sorted([j for j in direct if has_jd(j)], key=lambda x: -priority(x)))

    first, per_co = [], {}
    for j in described:
        if len(first) >= APPLY_FIRST_MAX or j["FitScore"] < APPLY_FIRST_MIN_SCORE:
            continue
        co = j["company"].lower()
        if per_co.get(co, 0) >= PER_COMPANY_CAP:
            continue
        per_co[co] = per_co.get(co, 0) + 1
        first.append(j)
    rest = [j for j in described if j not in first]
    more = [j for j in rest if j["FitScore"] >= 40]
    ref_low = [j for j in rest if j["FitScore"] < 40 and j.get("referral")]
    low = [j for j in rest if j["FitScore"] < 40 and not j.get("referral")]

    for group, sec in ((first, "A"), (more, "B"), (ref_low, "R"), (low, "C"),
                       (titleonly, "D"), (agency, "E")):
        for j in group:
            j["Section"] = sec
            for dup in j.get("also", []):
                dup["Section"] = sec

    def also(j):
        extra = j.get("also") or []
        if not extra:
            return ""
        locs = sorted({d["location"] for d in extra} - {j["location"]})
        return f" (+{len(extra)} identical posting{'s' if len(extra) > 1 else ''}" + \
               (f": {'; '.join(locs)[:80]}" if locs else "") + ")"

    tag = lambda j: " *REFERRAL*" if j.get("referral") else ""
    refs = sum(1 for j in jobs if j.get("referral"))
    lines = [f"JOB PIPELINE - {today}",
             f"New roles for a {MY_YOE}-year engineer, posted in the last {max_age} day(s)"
             if max_age > 0 else f"New roles for a {MY_YOE}-year engineer"]
    if refs:
        lines.append(f"{refs} at companies where you have a referral (marked *REFERRAL*)")
    lines += ["=" * 60, ""]

    lines.append(f"APPLY FIRST ({len(first)}) - best odds in this batch, "
                 f"max {PER_COMPANY_CAP} per company")
    lines.append("-" * 60)
    if not first:
        lines += ["  (nothing cleared the bar this batch)", ""]
    for n, j in enumerate(first, 1):
        kw = ", ".join(j["Skills"][:7]) or "none detected"
        lines += [
            f"{n}. [{j['FitScore']}]{tag(j)} {j['title']}",
            f"   {j['company']} | {j['location'][:60]}{also(j)} | {_age_label(j)}",
            f"   Years: {band_label(j.get('yoe_band'))} ({j.get('yoe_fit')})",
            f"   Mirror in your resume (you have these): {kw}",
        ]
        if j["Gaps"]:
            lines.append(f"   JD also wants: {', '.join(j['Gaps'])}")
        if j.get("applied_recently"):
            lines.append(f"   Note: {j['applied_recently']}")
        lines += [f"   -> {_action(j)}", f"   {j['url']}", ""]

    lines.append(f"MORE MATCHES ({len(more)})")
    lines.append("-" * 60)
    for j in more:
        note = f" [{j['applied_recently']}]" if j.get("applied_recently") else ""
        lines.append(f"[{j['FitScore']}]{tag(j)} {j['title']} - {j['company']}, "
                     f"{j['location'][:40]}{also(j)} | {band_label(j.get('yoe_band'))} "
                     f"| {_age_label(j)}{note}\n      {j['FitReason']}\n      {j['url']}")
    if not more:
        lines.append("  (none)")
    lines.append("")

    if ref_low:
        lines.append(f"REFERRAL COMPANIES, WEAK TEXT MATCH ({len(ref_low)})")
        lines.append("-" * 60)
        lines.append("  Your years fit, your stack barely shows in the JD. Worth a")
        lines.append("  glance only because a referral can carry a weaker match.")
        for j in ref_low:
            lines.append(f"  [{j['FitScore']}] {j['title']} - {j['company']} | "
                         f"{band_label(j.get('yoe_band'))}\n      {j['url']}")
        lines.append("")

    lines.append(f"LOW FIT: {len(low)} roles with full JDs scored under 40, not listed")
    lines.append("")

    lines.append(f"TITLE MATCH ONLY ({len(titleonly)})")
    lines.append("-" * 60)
    lines.append("  No JD text available (careers page, or the JD fetch failed).")
    lines.append("  Not scored on content - open the page to judge.")
    for j in titleonly:
        lines.append(f"  {j['title']}{tag(j)} - {j['company']}, {j['location'][:40]}"
                     f"\n      {j['url']}")
    if not titleonly:
        lines.append("  (none)")

    lines.append("")
    lines.append(f"AGENCY / CONSULTANCY ({len(agency)})")
    lines.append("-" * 60)
    lines.append("  Client company NOT named. Before applying, search the JD")
    lines.append("  text to identify the employer, then check whether they")
    lines.append("  have a direct board above. Applying through an agency to")
    lines.append("  a company you later apply to directly can disqualify you.")
    for j in agency:
        lines.append(f"  {j['title']} - via {j['company']}, {j['location']}"
                     f"\n      {j['url']}")
    if not agency:
        lines.append("  (none)")

    if followups:
        lines += ["", f"FOLLOW-UPS DUE ({len(followups)})", "-" * 60]
        for d, r in followups:
            how = ("ask your referrer for the status" if (r.get("Status") or "").lower()
                   == "referred" else "message the recruiter or hiring manager on "
                   "LinkedIn with the req link")
            lines.append(f"  {d}d: {r.get('Title') or '?'} - {r.get('Company') or '?'}: "
                         f"{how}\n      {r.get('URL')}")
        lines.append("  Update the status: Actions > jobscan > Run workflow > applied.")

    lines += [
        "",
        "=" * 60,
        f"Raw roles polled: {raw_count}",
        f"New after filters: {len(jobs)}",
        f"Skipped as older than {max_age} days: {stats.get('too_old', 0)}",
        f"Dropped, asks more than {MY_YOE + YOE_STRETCH} years: {stats.get('over_yoe', 0)}",
        f"Dropped, senior title without a band you fit: {stats.get('senior_unproven', 0)}",
        f"Dropped, graduation-year gated: {stats.get('batch_gated', 0)}",
        f"JDs fetched: {stats.get('enriched', 0)} (failed: {stats.get('enrich_failed', 0)})",
        f"No posting date from board: {stats.get('undated', 0)} (kept, dated by first sighting)",
        f"Fetch errors: {errors} (see fetch_errors.log)",
    ]
    return "\n".join(lines), {"first": first, "more": more, "ref_low": ref_low,
                              "low": low, "titleonly": titleonly, "agency": agency}


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


# Before 2026-09-25, Workday and Oracle roles were scored on their title alone
# and anything under 40 went into the Section C head count, never shown; and
# every SmartRecruiters link 404ed. Those rows are "found", not "seen".
LEGACY_TITLE_SCORED = re.compile(r"myworkdayjobs\.com|oraclecloud\.com")


def _settled(row):
    """True if this pipeline row was judged on real information and either
    shown to you or deliberately filtered - i.e. belongs in seen.json."""
    if (row.get("Section") or "").strip():
        return True
    url = row.get("URL") or ""
    if "jobs.smartrecruiters.com" in url:
        return False
    try:
        s = int(row.get("FitScore") or 0)
    except ValueError:
        s = 0
    return not (LEGACY_TITLE_SCORED.search(url) and s < 40)


def repair_seen():
    """Rebuild seen.json from pipeline.csv.

    seen.json is meant to record "already emailed to you". A bug in run() also
    wrote every role that was merely older than --max-age into it, so hundreds
    of open roles were marked read without ever being reported. pipeline.csv is
    the real record of what was sent, so rebuild from that - minus the rows
    that were never really shown (see _settled), so the next backlog run
    re-checks them with their full JD.
    """
    if not PIPELINE_CSV.exists():
        print("No pipeline.csv; nothing to repair.")
        return
    rows = read_pipeline()[0]
    reported = {canon_url(r["URL"]) for r in rows if r.get("URL") and _settled(r)}
    released = sum(1 for r in rows if r.get("URL") and not _settled(r))
    before = len(load_seen())
    save_seen(reported)
    print(f"seen.json rebuilt from pipeline.csv: {before} -> {len(reported)} "
          f"({released} hidden or broken-link roles released for a re-check).")


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
    ap.add_argument("--applied", metavar="URL",
                    help="record an application for this posting URL")
    ap.add_argument("--status", default="applied", choices=APPLIED_STATUSES,
                    help="with --applied: applied, referred, interview, ...")
    ap.add_argument("--note", default="", help="with --applied: free text")
    a = ap.parse_args()
    if a.applied:
        mark_applied(a.applied, a.status, a.note)
    elif a.prune:
        prune()
    elif a.diagnose:
        diagnose()
    elif a.repair_seen:
        repair_seen()
    else:
        run(dry_run=a.dry_run, reset=a.reset,
            max_age=BACKLOG_MAX_AGE if a.backlog else a.max_age)
