"""Run: python -m pytest tests/

Tailored resumes for APPLY FIRST roles (resumes.py). Claude is mocked
throughout: no API key or subscription token is used by these tests.
"""
import email
from collections import Counter
from unittest.mock import MagicMock, patch

import pytest

import jobscan as J
import resumes as R

JOB = {"title": "Backend Engineer", "company": "Acme", "location": "Bengaluru, India",
       "url": "https://jobs.example.com/1", "description": "Java, Spring Boot. 2-4 years. " * 20}


@pytest.fixture
def env(monkeypatch):
    for k in ("RESUME_PACKET", "RESUME_INSTRUCTIONS", "ANTHROPIC_API_KEY",
              "CLAUDE_CODE_OAUTH_TOKEN", "RESUME_MODEL", "RESUME_EFFORT", "RESUME_MAX"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RESUME_PACKET", "# PACKET\nName: Test Person")
    monkeypatch.setenv("RESUME_INSTRUCTIONS", "ELIGIBILITY GATE ...")
    return monkeypatch


def test_backend_needs_both_texts_and_one_credential(env):
    assert R.backend() is None
    env.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oat")
    assert R.backend() == "cli"
    env.setenv("ANTHROPIC_API_KEY", "sk")
    assert R.backend() == "api"            # the explicit pay-per-use key wins
    env.delenv("RESUME_INSTRUCTIONS")
    assert R.backend() is None


@pytest.mark.parametrize("text, kind, score", [
    ("RESULT: RESUME\n## SUMMARY\nBackend engineer", "resume", None),
    ("RESULT: AUDIT 72/100\nScore: 72", "audit", 72),
    ("  result: audit score 85 / 100\nTitle match 20/20", "audit", 85),
    # markdown wrapping and trailing text (pre-launch review, 2026-10-07)
    ("`RESULT: RESUME`\n## SUMMARY", "resume", None),
    ("**RESULT: AUDIT 72/100**\nTitle match", "audit", 72),
    ("RESULT: AUDIT 72/100 (years gate)\nx", "audit", 72),
    ("RESULT: AUDIT 72/100.\nx", "audit", 72),
    ("GUNAL ...\n## SUMMARY", "result", None),          # marker missing: kept as is
])
def test_parse_result_reads_the_marker_and_strips_it(text, kind, score):
    r = R.parse_result(text)
    assert (r["kind"], r["score"]) == (kind, score)
    if kind != "result":
        assert "RESULT:" not in r["text"].upper()


def test_prompt_carries_the_jd_and_the_marker_rule():
    p = R.user_prompt(JOB)
    assert p.startswith(R.MARKER_RULE)
    assert "Company: Acme" in p and "Spring Boot" in p and JOB["url"] in p


def test_api_call_caches_the_packet_and_opts_into_fallbacks(env):
    env.setenv("ANTHROPIC_API_KEY", "sk")
    resp = MagicMock(stop_reason="end_turn",
                     content=[MagicMock(type="text", text="RESULT: RESUME\nbody")])
    client = MagicMock()
    client.beta.messages.create.return_value = resp
    with patch("anthropic.Anthropic", return_value=client):
        r = R.generate(JOB)
    assert r == {"kind": "resume", "score": None, "text": "body"}
    kw = client.beta.messages.create.call_args.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "Test Person" in kw["system"][-1]["text"]
    assert kw["extra_body"] == {"fallbacks": "default"}
    assert kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"] == {"effort": "high"}


def test_api_refusal_becomes_an_error_not_a_crash(env):
    env.setenv("ANTHROPIC_API_KEY", "sk")
    client = MagicMock()
    client.beta.messages.create.return_value = MagicMock(stop_reason="refusal", content=[])
    with patch("anthropic.Anthropic", return_value=client):
        r = R.generate(JOB)
    assert r["kind"] == "error" and "declined" in r["error"]


def test_cli_call_uses_the_token_not_bare_mode(env):
    env.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oat")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        i = cmd.index("--system-prompt-file")
        seen["system"] = open(cmd[i + 1], encoding="utf-8").read()
        return MagicMock(returncode=0, stdout="RESULT: AUDIT 60/100\nYears: 4+", stderr="")
    with patch.object(R.subprocess, "run", side_effect=fake_run):
        r = R.generate(JOB)
    assert r["kind"] == "audit" and r["score"] == 60
    cmd = seen["cmd"]
    assert cmd[:2] == ["claude", "-p"] and "--bare" not in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "Test Person" in seen["system"]
    assert "Spring Boot" in seen["kw"]["input"]
    assert "/home/user/jobscan" not in seen["kw"]["cwd"]     # outside the repo


def test_cli_failure_is_reported(env):
    env.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oat")
    with patch.object(R.subprocess, "run",
                      return_value=MagicMock(returncode=1, stdout="", stderr="auth failed")):
        r = R.generate(JOB)
    assert r["kind"] == "error" and "auth failed" in r["error"]


def test_generate_all_skips_title_only_roles_and_respects_the_cap(env):
    env.setenv("ANTHROPIC_API_KEY", "sk")
    env.setenv("RESUME_MAX", "2")
    jobs = [dict(JOB, url=f"u{i}") for i in range(4)] + [dict(JOB, url="short", description="x")]
    with patch.object(R, "generate", return_value={"kind": "resume", "score": None, "text": "t"}):
        out = R.generate_all(jobs, J.has_jd)
    assert list(out) == ["u0", "u1"]


def test_generate_all_is_a_no_op_when_off(env):
    with patch.object(R, "generate") as gen:
        assert R.generate_all([JOB], J.has_jd) == {}
    gen.assert_not_called()


def test_digest_notes_and_attachments_are_built_per_role(env):
    env.setenv("ANTHROPIC_API_KEY", "sk")
    a, b = dict(JOB, url="a"), dict(JOB, url="b")      # same company and title
    results = {"a": {"kind": "resume", "score": None, "text": "resume A"},
               "b": {"kind": "error", "score": None, "text": "", "error": "Boom"}}
    with patch.object(R, "generate_all", return_value=results):
        attachments, notes = J.tailored_resumes([a, b])
    assert attachments == [("Acme - Backend Engineer.md", "resume A")]
    assert notes["a"] == "Resume attached: Acme - Backend Engineer.md"
    assert "not generated (Boom)" in notes["b"]


def test_email_carries_markdown_attachments(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "me@example.com")
    monkeypatch.setenv("SMTP_PASS", "pw")
    sent = {}
    smtp = MagicMock()
    smtp.__enter__.return_value.send_message.side_effect = lambda m: sent.setdefault("m", m)
    with patch.object(J.smtplib, "SMTP_SSL", return_value=smtp):
        J.send_email("Jobs", "digest body", [("Acme - Backend Engineer.md", "# Résumé")])
    msg = email.message_from_string(sent["m"].as_string())
    parts = [p for p in msg.walk() if not p.is_multipart()]
    assert parts[0].get_payload(decode=True).decode() == "digest body"
    assert parts[1].get_filename() == "Acme - Backend Engineer.md"
    assert parts[1].get_content_type() == "text/markdown"
    assert parts[1].get_content_charset() == "utf-8"
    assert parts[1].get_payload(decode=True).decode("utf-8") == "# Résumé"


def test_no_smtp_prints_the_digest_but_never_the_resumes(monkeypatch, capsys):
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASS", raising=False)
    J.send_email("Jobs", "digest body", [("x.md", "PHONE +91 0000000000")])
    out = capsys.readouterr().out
    assert "digest body" in out and "PHONE" not in out


def test_apply_first_line_shows_the_resume_status():
    j = {"title": "Backend Engineer", "company": "Acme", "location": "Bengaluru",
         "url": "https://jobs.example.com/1", "FitScore": 90, "Skills": ["java"], "Gaps": [],
         "FitReason": "", "GapNote": "", "yoe_band": (2, 4), "yoe_fit": "in-band",
         "description": "x" * 400, "posted": "2026-10-06", "age": 0}
    text, _ = J.build_digest([j], {}, 1, 0, "2026-10-06", 2,
                             resume_notes={j["url"]: "Resume attached: Acme - Backend Engineer.md"})
    first = text.split("MORE MATCHES")[0]
    assert "Resume attached: Acme - Backend Engineer.md" in first


def test_generation_has_a_total_budget(env, monkeypatch):
    import time
    env.setenv("ANTHROPIC_API_KEY", "sk")
    monkeypatch.setattr(R, "BUDGET_SECONDS", 0.3)

    def slow(job):
        time.sleep(0.05 if job["url"] == "fast" else 2)
        return {"kind": "resume", "score": None, "text": "t"}
    with patch.object(R, "generate", side_effect=slow):
        out = R.generate_all([dict(JOB, url="fast"), dict(JOB, url="slow")], J.has_jd)
    assert out["fast"]["kind"] == "resume"
    assert out["slow"]["kind"] == "error" and "budget" in out["slow"]["error"]


def test_rebuilding_the_digest_does_not_double_identical_postings():
    def job(city):
        return {"title": "Backend Engineer", "company": "Acme", "location": city,
                "url": f"https://x/{city}", "FitScore": 90, "Skills": ["java"], "Gaps": [],
                "FitReason": "", "GapNote": "", "yoe_band": (2, 4), "yoe_fit": "in-band",
                "description": "Same JD for every city. " * 30, "posted": "2026-10-06", "age": 0}
    jobs = [job("Bengaluru"), job("Hyderabad"), job("Pune")]
    first, _ = J.build_digest(jobs, {}, 3, 0, "2026-10-06", 2)
    second, _ = J.build_digest(jobs, {}, 3, 0, "2026-10-06", 2,
                               resume_notes={"https://x/Bengaluru": "Resume attached: a.md"})
    assert "+2 identical postings" in first and "+2 identical postings" in second
    assert "+4" not in second


@pytest.mark.parametrize("smtp, called", [(False, False), (True, True)])
def test_no_smtp_means_no_paid_generation(env, monkeypatch, smtp, called):
    env.setenv("ANTHROPIC_API_KEY", "sk")
    if smtp:
        env.setenv("SMTP_USER", "me@example.com")
        env.setenv("SMTP_PASS", "pw")
    else:
        env.delenv("SMTP_USER", raising=False)
        env.delenv("SMTP_PASS", raising=False)
    j = {"title": "Backend Engineer", "company": "Acme", "location": "Bengaluru",
         "url": "https://x/1", "FitScore": 90, "Skills": ["java"], "Gaps": [], "FitReason": "",
         "GapNote": "", "yoe_band": (2, 4), "yoe_fit": "in-band", "description": "JD " * 200,
         "posted": "2026-10-06", "age": 0, "referral": False}
    with patch.object(J, "load_companies", return_value=[]), \
            patch.object(J, "poll_boards", return_value=([], 0)), \
            patch.object(J, "select", return_value=([j], [], Counter())), \
            patch.object(J, "load_seen", return_value=set()), \
            patch.object(J, "load_applied", return_value=[]), \
            patch.object(J, "load_health", return_value={}), \
            patch.object(J, "save_health"), patch.object(J, "save_seen"), \
            patch.object(J, "upsert_pipeline"), patch.object(J, "_trim_errors_log"), \
            patch.object(J, "_daily_due", return_value=False), \
            patch.object(J, "send_email"), \
            patch.object(J, "tailored_resumes", return_value=([], {})) as gen:
        J.run(dry_run=False, max_age=2)
    assert gen.called is called
