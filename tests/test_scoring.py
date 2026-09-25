"""Run: python -m pytest tests/

Years-of-experience cases are lines lifted from live JDs (2026-09-25), with
the value the old min()-of-everything parser produced noted where it was
wrong. MY_YOE is pinned to 2 here so the tests do not move with your profile.
"""
import pytest

import jobscan as J


@pytest.fixture(autouse=True)
def two_years(monkeypatch):
    monkeypatch.setattr(J, "MY_YOE", 2)
    monkeypatch.setattr(J, "YOE_STRETCH", 1)
    monkeypatch.setattr(J, "HAS_MASTERS", False)


@pytest.mark.parametrize("title, desc, band", [
    # title band wins over a generic body template (Visa)
    ("Software Engineer (1 - 2 years of experience in Python, GenAI)",
     "Bachelor's degree, OR 3+ years of relevant work experience", (1, 2)),
    ("Software Engineer - Python, UI, AI, Exp: 4-8 Yrs, Bangalore", "", (4, 8)),
    ("Software Engineering Technical Leader - 12+yrs - C/C++", "", (12, None)),
    # highest required minimum, not the lowest number (Amazon; old: 2)
    ("Software Development Engineer II",
     "- 3+ years of non-internship professional software development experience\n"
     "- 2+ years of non-internship design or architecture (design patterns)", (3, None)),
    # "yrs" and a typo (HPE; old: None)
    ("Fullstack Developer", "1-2 yrs of experinece is required", (1, 2)),
    # no "+" at all (Cisco; old: None) and Master's/PhD routes ignored
    ("Software Engineer",
     "Bachelors + 7 years of related experience OR Masters + 4 years of related "
     "experience OR PhD + 1 year of related experience", (7, None)),
    # Bachelor's route among alternatives (Visa; old: 2)
    ("Software Engineer",
     "8+ years of relevant work experience with a Bachelor's Degree or at least 5 "
     "years of experience with an Advanced Degree (e.g. Masters, MBA, JD, MD) or 11+ "
     "years of relevant work experience\nAt least 2 years of experience with AWS Cloud",
     (8, None)),
    # "BE or BTech" and "BTech / MTech" are one route, not alternatives
    ("Software Engineer", "A minimum of B.E or BTech from a reputed institution in "
     "CS/EE with 3+ years of experience is required.", (3, None)),
    ("Software Engineer", "BTech / MTech in CS/CE or related field with 5+ years "
     "proven experience.", (5, None)),
    ("Senior Software Engineer", "Bachelor's or Master's degree in computer science or "
     "related technical field with 6-8 years of full stack experience", (6, 8)),
    # "or related field" is not an alternative route
    ("Software Engineer", "Bachelor's degree in Computer Science, Cybersecurity, or "
     "related field and 5+ years of relevant experience; or", (5, None)),
    # a degree OR years: the degree alone satisfies the line
    ("Software Engineer", "Bachelor's degree, OR 3+ years of relevant work experience",
     None),
    # Walmart "Option 2" is the no-degree route
    ("Software Engineer III", "Minimum Qualifications:Option 1: Bachelor's degree in "
     "computer science and 2years' experience in software engineering.\n"
     "Option 2: 4 years' experience in software engineering.", (2, None)),
    # company history is not a requirement
    ("Software Engineer", "We've been innovating fearlessly for 40 years to create "
     "solutions.\nMeesho's user base has grown 4x in the last 1 year and we have more "
     "than 50 million downloads", None),
    # connector words without "experience" (Sarvam)
    ("ML Ops Engineer", "3–5 years in ML engineering or MLOps", (3, 5)),
    ("Backend Engineer", "Experience:  Minimum of 2-3 years of professional experience",
     (2, 3)),
    ("Backend Engineer", "At least two years experience in Java 17+", (2, None)),
    # preferred / nice-to-have lines do not gate
    ("Backend Engineer", "Requirements\n2+ years of backend experience\n"
     "Preferred Qualifications\n5+ years of Kafka experience", (2, None)),
    ("Backend Engineer", "2+ years of backend experience\n4+ years of Go is a plus",
     (2, None)),
    # a domain preference after the years does not make them optional
    ("Backend Engineer", "3+ years of experience, preferably in fintech", (3, None)),
    # a semicolon joins requirements
    ("Backend Engineer", "Bachelor's degree in CS; 5+ years of experience", (5, None)),
])
def test_yoe_band(title, desc, band):
    assert J.yoe_band(title, desc) == band


@pytest.mark.parametrize("band, fit", [
    ((0, 2), "in-band"), ((1, 3), "in-band"), ((2, None), "in-band"), ((2, 5), "in-band"),
    ((3, None), "stretch"), ((3, 6), "stretch"),
    ((4, None), "over"), ((5, 8), "over"),
    ((0, 1), "below"), (None, "unstated"),
])
def test_band_fit(band, fit):
    assert J.band_fit(band) == fit


def test_skill_matching_is_whole_word():
    blob = ("we leverage storage coverage to encourage average laws and enrollment "
            "with expressive reactive javascript").lower()
    got = [s for s, _ in J.skill_hits(blob)]
    for fp in ("rag", "aws", "llm", "express.js", "react", "java"):
        assert fp not in got
    assert "javascript" in got


def test_skill_plurals_and_aliases():
    got = dict(J.skill_hits("microservices, embeddings, llms, postgresql (postgres), "
                            "k8s and spring boot"))
    assert {"microservice", "embedding", "llm", "postgresql", "kubernetes",
            "spring boot", "spring"} <= set(got)
    assert "postgres" not in got          # one skill, not two


@pytest.mark.parametrize("text, has_go", [
    ("Java, Go, Python", True), ("Go/Python services", True), ("Golang", True),
    ("Google and go-to-market, 3 days ago", False),
])
def test_gap_go(text, has_go):
    assert ("go" in J.gap_skills(text.lower())) is has_go


@pytest.mark.parametrize("title, senior", [
    ("Senior Software Engineer", True), ("Sr. Backend Engineer", True),
    ("SENIOR, SOFTWARE ENGINEER", True), ("Software Engineer III", True),
    ("SDE-3", True), ("Software Engineer II", False), ("SDE 2", False),
    ("Platform Test Engineer - L2/L3 Protocols", False),
])
def test_senior_title(title, senior):
    assert J.is_senior_title(title) is senior


@pytest.mark.parametrize("title, ok", [
    ("LLM Engineer", True), ("Forward Deployed Engineer", True),
    ("Product Engineer", True), ("MLOps Engineer", True),
    ("Software Engineering Technical Leader", False),
    ("Graduate Software Engineer", False), ("Graduate Engineer Trainee", False),
    ("Java Developer - AVP", False), ("Sales Manager II", False),
])
def test_title_ok(title, ok):
    assert J.title_ok(title) is ok


def test_batch_gate():
    y = J._THIS_YEAR
    assert J.BATCH_GATE_RE.search(f"Open to {y} batch graduates only")
    assert J.BATCH_GATE_RE.search(f"Candidates graduating in {y + 1}")
    assert not J.BATCH_GATE_RE.search(f"Founded in {y - 10}, graduates welcome")


def test_score_ranks_in_band_stack_match_above_stretch():
    jd = ("We build RAG and agentic systems in Python with FastAPI, LangGraph, "
          "pgvector and Postgres on AWS with Docker and Kubernetes. " * 3)
    a = {"title": "AI Engineer", "description": jd, "yoe_band": (1, 3), "yoe_fit": "in-band"}
    b = {"title": "AI Engineer", "description": jd, "yoe_band": (3, None), "yoe_fit": "stretch"}
    assert J.score(a) > J.score(b) >= 70
    assert "rag" in a["Skills"] and a["FitReason"].startswith("Overlap:")


def test_smartrecruiters_legacy_url_is_canonicalised():
    old = "https://jobs.smartrecruiters.com/SWIGGY/postings/6000000001394755"
    assert J.canon_url(old) == "https://jobs.smartrecruiters.com/SWIGGY/6000000001394755"
    ok = "https://job-boards.greenhouse.io/x/jobs/1"
    assert J.canon_url(ok) == ok
