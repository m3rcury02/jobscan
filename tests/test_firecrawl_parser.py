"""Run: python -m pytest tests/  (fixtures are real Firecrawl output, 2026-09-15)"""
from pathlib import Path
import jobscan as J

FX = Path(__file__).parent

def jobs(name):
    return J.parse_markdown_jobs((FX / f"fx_{name}.md").read_text())

def test_goldman_cards_keep_per_role_urls():
    urls = {j["url"] for j in jobs("goldman")}
    assert "https://higher.gs.com/roles/179358" in urls

def test_markdown_escapes_removed():
    assert "Software Engineer - C# WPF" in {j["title"] for j in jobs("siemens")}

def test_apply_and_alert_links_ignored():
    got = jobs("tesco")
    assert len(got) == 1 and got[0]["location"] == "Bengaluru"

def test_non_engineering_titles_dropped():
    assert [j["title"] for j in jobs("satsure")] == ["Software Development Engineer - 2"]

def test_lilly_phenom_cards_title_first():
    # Phenom card markdown puts title on its own line above a separate
    # "Location\n<city>" block, unlike Tesco/Siemens/Goldman's single-line
    # cards - this is the shape most Phenom sites use.
    got = {j["title"]: j["location"] for j in jobs("lilly")}
    assert got["Sr. Principal Machine Learning Engineer"] == "Bangalore"
    assert got["Associate Director, Data Engineering"] == "Bangalore"
    # Manufacturing/Sales roles on the same page are titles, but off-target
    # for this scanner's engineering filter - excluded here is correct.
    assert "Territory Manager - Immunology & Neurology" not in got
