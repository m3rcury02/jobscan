"""Run: python -m pytest tests/

Enrichment, filtering, the digest and the state files, with the network
mocked and every state file redirected to a temp dir.
"""
import csv
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import jobscan as J

JD_OK = ("You will build backend services in Java with Spring Boot, Kafka and "
         "PostgreSQL, deployed with Docker and Kubernetes on AWS. " * 3)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name, fn in (("PIPELINE_CSV", "pipeline.csv"), ("SEEN_JSON", "seen.json"),
                     ("APPLIED_CSV", "applied.csv"), ("ERRORS_LOG", "errors.log")):
        monkeypatch.setattr(J, name, tmp_path / fn)
    monkeypatch.setattr(J, "MY_YOE", 2)
    monkeypatch.setattr(J, "YOE_STRETCH", 1)
    return tmp_path


def wd_job(title, loc="Bengaluru, India", company="Cisco", referral=True, n=1):
    return {"title": title, "location": loc, "company": company, "referral": referral,
            "url": f"https://cisco.wd5.myworkdayjobs.com/en-US/C/job/{n}",
            "description": "", "posted": datetime.utcnow().strftime("%Y-%m-%d"),
            "_detail": ("workday", f"https://cisco.wd5.myworkdayjobs.com/wday/cxs/c/C/job/{n}")}


def workday_detail(desc, loc="Bangalore, India", extra=()):
    return {"jobPostingInfo": {"jobDescription": f"<p>{desc}</p>", "location": loc,
                               "additionalLocations": list(extra),
                               "startDate": datetime.utcnow().strftime("%Y-%m-%d")}}


def test_enrich_workday_fills_jd_location_and_date():
    j = wd_job("Software Engineer")
    with patch.object(J, "get_json", return_value=workday_detail(JD_OK, extra=["Pune, India"])):
        assert J.enrich(j)
    assert J.has_jd(j) and "Kafka" in j["description"]
    assert j["location"] == "Bangalore, India; Pune, India"


def test_enrich_failure_is_not_fatal():
    j = wd_job("Software Engineer")
    with patch.object(J, "get_json", side_effect=RuntimeError("429")):
        assert not J.enrich(j)
    assert not J.has_jd(j) and "429" in j["_enrich_error"]


def test_select_filters_on_the_fetched_jd():
    jobs = [
        wd_job("Software Engineer", n=1),                     # 2-4 yrs: keep
        wd_job("Software Engineer", n=2),                     # 6+ yrs: drop
        wd_job("Senior Software Engineer", n=3),              # senior, unstated: drop
        wd_job("Senior Software Engineer", n=4),              # senior, 2-5: keep
        wd_job("Software Engineer", loc="51 Locations", n=5),  # all US: drop
    ]
    details = {
        "1": workday_detail("2-4 years of software engineering experience. " + JD_OK),
        "2": workday_detail("6+ years of software engineering experience. " + JD_OK),
        "3": workday_detail(JD_OK),
        "4": workday_detail("2-5 years of experience. " + JD_OK),
        "5": workday_detail(JD_OK, loc="San Jose, California, US",
                            extra=["Remote - Texas, USA"]),
    }
    with patch.object(J, "get_json", side_effect=lambda url, retries=2: details[url[-1]]):
        kept, drops, stats = J.select(jobs, seen=set(), max_age=2)
    assert sorted(j["url"][-1] for j in kept) == ["1", "4"]
    assert stats["over_yoe"] == 1 and stats["senior_unproven"] == 1
    assert {u[-1] for u, _ in drops} == {"2", "3"}
    assert all(j["FitScore"] >= 70 for j in kept)


def _kept(title, company, score, referral=False, jd=JD_OK, loc="Bengaluru", n=0):
    return {"title": title, "company": company, "location": loc, "referral": referral,
            "url": f"https://x/{company}/{title}/{n}", "description": jd, "age": 0,
            "FitScore": score, "FitReason": "Overlap: java", "Skills": ["java"],
            "Gaps": [], "yoe_band": (2, 4), "yoe_fit": "in-band", "GapNote": ""}


def test_digest_caps_per_company_and_dedupes_identical_postings():
    jobs = [_kept(f"Backend Engineer {i}", "Cisco", 90 - i, referral=True, n=i)
            for i in range(4)]
    jobs += [_kept("Backend Engineer", "Walmart", 80, loc="Chennai", n=1),
             _kept("Backend Engineer", "Walmart", 80, loc="Bengaluru", n=2)]
    text, sec = J.build_digest(jobs, {}, 100, 0, "2026-09-25", 2)
    first = [j["company"] for j in sec["first"]]
    assert first.count("Cisco") == J.PER_COMPANY_CAP
    assert first.count("Walmart") == 1              # the twin is folded in
    assert "+1 identical posting: Chennai" in text or "+1 identical posting: Bengaluru" in text
    assert "let them submit you BEFORE you apply" in text
    assert all(j.get("Section") for j in jobs)      # every row gets a section


def test_referral_roles_are_listed_never_just_counted():
    jobs = [_kept("Software Engineer", "Cisco", 35, referral=True),
            _kept("Software Engineer", "Acme", 35)]
    text, sec = J.build_digest(jobs, {}, 10, 0, "2026-09-25", 2)
    assert [j["company"] for j in sec["ref_low"]] == ["Cisco"]
    assert "https://x/Cisco/" in text and "https://x/Acme/" not in text


def test_upsert_keeps_user_columns_and_one_row_per_url(isolated):
    J.upsert_pipeline([{"URL": "u1", "DateSeen": "2026-09-01", "FitScore": 30, "Status": "New"}])
    J.upsert_pipeline([{"URL": "u1", "Status": "Applied", "AppliedDate": "2026-09-02"}],
                      user=True)
    J.upsert_pipeline([{"URL": "u1", "DateSeen": "2026-09-25", "FitScore": 88,
                        "Status": "New", "Section": "A"},
                       {"URL": "u2", "FitScore": 50}], drops=[("u9", "x")])
    rows = {r["URL"]: r for r in csv.DictReader(open(isolated / "pipeline.csv"))}
    assert list(rows) == ["u1", "u2"]
    assert rows["u1"]["FitScore"] == "88" and rows["u1"]["Section"] == "A"
    assert rows["u1"]["Status"] == "Applied" and rows["u1"]["DateSeen"] == "2026-09-01"


def test_repair_seen_releases_title_scored_and_broken_link_rows(isolated):
    rows = [
        {"URL": "https://cisco.wd5.myworkdayjobs.com/a", "FitScore": "34"},    # hidden
        {"URL": "https://cisco.wd5.myworkdayjobs.com/b", "FitScore": "55"},    # shown
        {"URL": "https://jobs.smartrecruiters.com/X/postings/1", "FitScore": "40"},
        {"URL": "https://job-boards.greenhouse.io/y/1", "FitScore": "20"},     # real JD
        {"URL": "https://cisco.wd5.myworkdayjobs.com/c", "FitScore": "30", "Section": "C"},
    ]
    J.upsert_pipeline(rows)
    J.repair_seen()
    seen = J.load_seen()
    assert seen == {"https://cisco.wd5.myworkdayjobs.com/b",
                    "https://job-boards.greenhouse.io/y/1",
                    "https://cisco.wd5.myworkdayjobs.com/c"}


def test_mark_applied_and_followups(isolated):
    J.upsert_pipeline([{"URL": "https://x/1", "Company": "Cisco", "Title": "SWE",
                        "Status": "New"}])
    J.mark_applied("https://x/1", "referred")
    applied = J.load_applied()
    assert applied[0]["Company"] == "Cisco" and applied[0]["Via"] == "referral"
    pipe = {r["URL"]: r for r in J.read_pipeline()[0]}
    assert pipe["https://x/1"]["Status"] == "Referred"

    old = (datetime.now(J.IST) - timedelta(days=9)).strftime("%Y-%m-%d")
    applied[0]["Date"] = old
    due = J.followups_due(applied)
    assert len(due) == 1 and due[0][0] == 9
    assert "cisco" in J.recent_by_company(applied)
    applied[0]["Status"] = "rejected"
    assert J.followups_due(applied) == []
