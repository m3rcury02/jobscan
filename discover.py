#!/usr/bin/env python3
"""Find the ATS board for each company in wanted.txt, verify it, add it.

Why this runs in CI rather than on a laptop: probing is network-heavy and some
Workday shards (wd101, wd2) and corporate hosts are unreachable from sandboxed
or proxied networks, which silently under-reports. The Actions runner has an
unrestricted egress path, so discovery belongs there.

  wanted.txt   one company per line. "Name" or "Name | referral".
               # comments and blank lines ignored.

A slug is only written after two separate checks:
  1. the endpoint returns 200 with at least one posting, and
  2. the board's own company name matches the name you asked for.

Step 2 is the important one. Guessing slugs from company names is how this
repo ended up with 234 dead rows, and a live slug still routinely belongs to
someone else - greenhouse/tcs is a UK clinical staffing firm, greenhouse/pine
is a Canadian mortgage broker.
"""
import csv, io, re, sys, datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import jobscan as J

ROOT = Path(__file__).parent
WANTED = ROOT / "wanted.txt"

CHECK = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{t}/jobs",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{t}",
    "lever": "https://api.lever.co/v0/postings/{t}?mode=json",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{t}/postings?limit=1",
}
WDS = ["wd1", "wd5", "wd3", "wd103", "wd105", "wd2", "wd10", "wd12", "wd101"]
SUFFIX = re.compile(r"\b(technologies|technology|labs|inc|ltd|limited|pvt|private|"
                    r"solutions|systems|software|india|global|group|corp|company|co|"
                    r"analytics|digital|studios|ventures)\b", re.I)
S = requests.Session()
S.headers.update(J.HEADERS)


def variants(name):
    n = re.sub(r"\(.*?\)", " ", name.lower())
    n = re.sub(r"[.’'&]", " ", n)
    base = re.sub(r"[^a-z0-9]+", " ", n).strip()
    out = []
    for b in (base, SUFFIX.sub(" ", base).strip()):
        if not b:
            continue
        w = b.split()
        for c in ["".join(w), "-".join(w), w[0],
                  "".join(w[:2]) if len(w) > 1 else "",
                  "-".join(w[:2]) if len(w) > 1 else ""]:
            c = c.strip("-")
            if c and 2 <= len(c) <= 40 and c not in out:
                out.append(c)
    return out[:6]


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def owner(ats, tok):
    """Who does this board actually belong to? Empty string when unknowable."""
    try:
        if ats == "greenhouse":
            return S.get(f"https://boards-api.greenhouse.io/v1/boards/{tok}",
                         timeout=15).json().get("name", "")
        if ats == "smartrecruiters":
            d = S.get(f"https://api.smartrecruiters.com/v1/companies/{tok}/postings?limit=1",
                      timeout=15).json()
            return (d.get("content") or [{}])[0].get("company", {}).get("name", "")
    except Exception:
        return ""
    return ""          # ashby/lever/workday expose no board name; fall back to locations


def plausible(name, ats, tok, jobs):
    """Two independent gates, and a board must clear both.

    India roles is the hard gate. Every board here exists to surface Indian
    openings, so one with none is worthless even when it is the right company -
    and worthless is also exactly what a wrong-company match looks like.

    Name matching alone is not enough and must never stand on its own:
    greenhouse/diligent reports itself as "Diligent Services", which passes any
    prefix test against "Diligent" while actually hiring crane operators and
    plumbing foremen in Florida. Its zero Indian roles are what give it away.
    """
    india = [j for j in jobs if J.is_india(j["location"])
             and not J.is_non_india_only(j["location"])]
    if not india:
        return False, "no India roles"
    who = owner(ats, tok)
    if who and _norm(who)[:5] and _norm(name)[:5] not in _norm(who) \
            and _norm(who)[:5] not in _norm(name):
        return False, f"board belongs to {who}"
    return True, (who or f"{len(india)} India roles")


def probe_ats(name):
    for tok in variants(name):
        for ats, url in CHECK.items():
            try:
                r = S.get(url.format(t=tok), timeout=8)
            except Exception:
                continue
            if r.status_code != 200:
                continue
            try:
                jobs = J.ADAPTERS[ats](tok)
            except Exception:
                continue
            if not jobs:
                continue
            ok, who = plausible(name, ats, tok, jobs)
            if ok:
                return {"ats": ats, "token": tok, "tenant": "", "who": who,
                        "jobs": jobs}
    return None


def probe_workday(name):
    """422 = wrong host. 404 = right host, wrong site. 200 = both right."""
    body = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "India"}
    for t in dict.fromkeys([re.sub(r"[^a-z0-9]", "", name.lower()),
                            re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()[0]]):
        if not 2 <= len(t) <= 30:
            continue
        host = None
        for wd in WDS:
            try:
                if S.post(f"https://{t}.{wd}.myworkdayjobs.com/wday/cxs/{t}/ZZPROBE/jobs",
                          json=body, timeout=10).status_code == 404:
                    host = f"{t}.{wd}"
                    break
            except Exception:
                continue
        if not host:
            continue
        C = t.capitalize()
        for site in ["External", "External_Career_Site", "ExternalCareerSite",
                     "Careers", "careers", "External_Careers", "jobs", "Jobs",
                     f"{C}_Careers", f"{C}Careers", f"{C}", f"{C}ExternalCareerSite",
                     f"{C}_External_Career_Site", t, "Search"]:
            try:
                jobs = J.fetch_workday(site, host)
            except Exception:
                continue
            if not jobs:
                continue
            ok, who = plausible(name, "workday", site, jobs)
            if ok:
                return {"ats": "workday", "token": site, "tenant": host,
                        "who": who, "jobs": jobs}
    return None


def load_wanted():
    if not WANTED.exists():
        print(f"No {WANTED.name}; nothing to discover.")
        return []
    out = []
    for line in WANTED.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        out.append((parts[0], len(parts) > 1 and parts[1].lower().startswith("ref")))
    return out


def main():
    wanted = load_wanted()
    if not wanted:
        return
    rows = list(csv.reader(io.StringIO(
        (ROOT / "companies.csv").read_text(encoding="utf-8"))))
    hdr = rows[0]
    have = {(r[1].strip().lower(), r[2].strip().lower())
            for r in rows[1:] if len(r) >= 3 and r[2].strip()
            and not r[0].lstrip().startswith("#")}
    known = {_norm(r[0]) for r in rows[1:] if r and not r[0].lstrip().startswith("#")}

    todo = [(n, ref) for n, ref in wanted if _norm(n) not in known]
    print(f"{len(wanted)} wanted, {len(todo)} not already in companies.csv\n")

    def work(item):
        n, ref = item
        return n, ref, (probe_ats(n) or probe_workday(n))

    found, missing = [], []
    with ThreadPoolExecutor(max_workers=12) as pool:
        for n, ref, hit in pool.map(work, todo):
            if not hit:
                missing.append(n)
                continue
            if (hit["ats"], hit["token"].lower()) in have:
                print(f"  {n[:24]:26} already covered by {hit['ats']}/{hit['token']}")
                continue
            ind = [j for j in hit["jobs"] if J.is_india(j["location"])
                   and not J.is_non_india_only(j["location"])]
            mat = [j for j in ind if J.title_ok(j["title"])]
            found.append((n, ref, hit, len(ind), len(mat)))
            print(f"  FOUND {n[:22]:24} {hit['ats']:16} {hit['token'][:24]:26} "
                  f"{len(ind):4} India {len(mat):3} match   [{hit['who']}]")

    if found:
        ri = hdr.index("Referral") if "Referral" in hdr else None
        lines = (ROOT / "companies.csv").read_text(encoding="utf-8").split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
        lines.append(f"# --- discovered {datetime.date.today()} from wanted.txt ---")
        for n, ref, hit, ind, mat in sorted(found):
            row = [n, hit["ats"], hit["token"], hit["tenant"],
                   f"discovered; {ind} India roles, {mat} matching"]
            row += [""] * (len(hdr) - len(row))
            if ri is not None and ref:
                row[ri] = "yes"
            b = io.StringIO()
            csv.writer(b, lineterminator="").writerow(row[:len(hdr)])
            lines.append(b.getvalue())
        (ROOT / "companies.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\nadded {len(found)} boards; no board found for {len(missing)}")
    if missing:
        print("  " + ", ".join(sorted(missing)[:40]))
        print("  These run their own portal. Each needs a bespoke adapter -")
        print("  see fetch_amazon() for the shape one takes.")


if __name__ == "__main__":
    main()
