"""Tailored resumes for the digest's APPLY FIRST roles.

Each APPLY FIRST role that has a job description goes to Claude together with
your resume source packet and your generator instructions - the same two texts
as your claude.ai Project - and what comes back (a one-page resume, or the
eligibility audit when the gate scores under 90) is attached to the digest
email. Nothing is written to the repo or printed to the Actions log: the repo
and its logs are public, and both texts and every resume carry your phone
number and email.

Repo secrets (no RESUME_PACKET or no way to reach Claude = feature off):
  RESUME_PACKET            the resume source packet (markdown)
  RESUME_INSTRUCTIONS      the Project instructions
and one way to reach Claude:
  CLAUDE_CODE_OAUTH_TOKEN  your Claude subscription, through the Claude Code
                           CLI. Create it with `claude setup-token`. No extra
                           cost; it draws on your plan's usage limits.
  ANTHROPIC_API_KEY        the Anthropic API, paid per use. Wins when both
                           are set.
Optional repository variables: RESUME_MODEL (API default claude-opus-5-5;
CLI default is your plan's default model), RESUME_EFFORT (default high),
RESUME_MAX (default 8 per run).
"""
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, wait

API_MODEL = "claude-opus-5-5"
# Opus 5.5 defaults to medium effort. Fitting a JD under word, metric and
# one-system rules is reasoning-heavy, so ask for high.
DEFAULT_EFFORT = "high"
WORKERS = 3
# Per call and in total. A full resume measured 149 s; the budget keeps a
# stalled call from pushing the run past the workflow timeout, which would
# skip "Commit state" and pay for the same resumes again next hour.
CALL_TIMEOUT = 300
BUDGET_SECONDS = 15 * 60
# The marker line, possibly wrapped in markdown (`...` or **...**); the score
# is read from the rest of the line separately.
RESULT_LINE = re.compile(r"^[ \t]*[`*_]*[ \t]*RESULT:[ \t]*(RESUME|AUDIT)\b([^\n]*)$", re.I | re.M)
SCORE = re.compile(r"(\d{1,3})\s*/\s*100")

# Asked for on top of the user's own instructions, so the digest can say
# whether a resume or an audit came back without guessing from the text.
MARKER_RULE = ("Before anything else, write exactly one plain line, with no markdown: "
               "RESULT: RESUME if you generated the resume, or RESULT: AUDIT <score>/100 if "
               "the eligibility gate stopped you. Then give your normal output.")


def _env(name):
    return (os.environ.get(name) or "").strip()


def backend():
    """'api', 'cli' or None. None means the feature is off."""
    if not (_env("RESUME_PACKET") and _env("RESUME_INSTRUCTIONS")):
        return None
    if _env("ANTHROPIC_API_KEY"):
        return "api"
    if _env("CLAUDE_CODE_OAUTH_TOKEN"):
        return "cli"
    return None


def enabled():
    return backend() is not None


def max_per_run():
    try:
        return max(0, int(_env("RESUME_MAX") or 8))
    except ValueError:
        return 8


def system_text():
    """Instructions, then the packet as the Project's knowledge file. The
    packet goes last so the API caches both as one prefix."""
    return (_env("RESUME_INSTRUCTIONS") + "\n\n"
            "# KNOWLEDGE FILE: resume-source-packet.md\n\n" + _env("RESUME_PACKET"))


def user_prompt(job):
    """The JD as you would paste it into the Project, plus the result marker."""
    head = [f"Job title: {job.get('title', '')}",
            f"Company: {job.get('company', '')}",
            f"Location: {job.get('location', '')}",
            f"Posting: {job.get('url', '')}"]
    return (MARKER_RULE + "\n\nJOB DESCRIPTION\n" + "\n".join(head) + "\n\n"
            + (job.get("description") or "").strip())


def parse_result(text):
    """{'kind': 'resume'|'audit'|'result', 'score': int|None, 'text': str}.
    'result' means the marker line was missing; the text is kept as is."""
    m = RESULT_LINE.search(text or "")
    if not m:
        return {"kind": "result", "score": None, "text": (text or "").strip()}
    body = (text[:m.start()] + text[m.end():]).strip()
    sm = SCORE.search(m.group(2))
    return {"kind": m.group(1).lower(), "score": int(sm.group(1)) if sm else None,
            "text": body}


def _generate_api(job):
    import anthropic
    client = anthropic.Anthropic(max_retries=1, timeout=CALL_TIMEOUT)
    resp = client.beta.messages.create(
        model=_env("RESUME_MODEL") or API_MODEL,
        max_tokens=16000,
        system=[{"type": "text", "text": system_text(),
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_prompt(job)}],
        output_config={"effort": _env("RESUME_EFFORT") or DEFAULT_EFFORT},
        # A safety-classifier decline is re-run server-side on the model
        # Anthropic recommends for that category instead of coming back empty.
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("declined by the model")
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    if resp.stop_reason == "max_tokens":
        text += "\n\n[cut off at the output limit - regenerate this one by hand]"
    return text


def _generate_cli(job):
    """Claude Code headless, signed in with the subscription token. Not --bare:
    bare mode only reads ANTHROPIC_API_KEY and ignores the OAuth token."""
    with tempfile.TemporaryDirectory() as tmp:
        sys_file = os.path.join(tmp, "system.md")
        with open(sys_file, "w", encoding="utf-8") as f:
            f.write(system_text())
        cmd = ["claude", "-p", "--system-prompt-file", sys_file, "--tools", "",
               "--output-format", "text", "--no-session-persistence",
               "--effort", _env("RESUME_EFFORT") or DEFAULT_EFFORT]
        if _env("RESUME_MODEL"):
            cmd += ["--model", _env("RESUME_MODEL")]
        # cwd outside the repo, so no CLAUDE.md or project settings load
        r = subprocess.run(cmd, input=user_prompt(job), capture_output=True, text=True,
                           cwd=tmp, timeout=CALL_TIMEOUT)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or ["no output"]
        raise RuntimeError(f"claude exited {r.returncode}: {tail[0][:200]}")
    return r.stdout


def generate(job):
    """One job -> {'kind', 'score', 'text'} or {'kind': 'error', 'error'}.
    Never raises: a failed resume must not cost you the digest."""
    try:
        fn = _generate_api if backend() == "api" else _generate_cli
        return parse_result(fn(job))
    except Exception as e:  # noqa: BLE001 - reported in the digest instead
        return {"kind": "error", "score": None, "text": "",
                "error": f"{type(e).__name__}: {e}"[:240]}


def generate_all(jobs, has_jd):
    """{url: result} for the first max_per_run() jobs that have a JD. Jobs
    without one are left out: a resume tailored to a title alone would be a
    guess dressed as a fit."""
    todo = [j for j in jobs if has_jd(j)][:max_per_run()]
    if not todo or not enabled():
        return {}
    pool = ThreadPoolExecutor(max_workers=WORKERS)
    futures = {pool.submit(generate, j): j for j in todo}
    done, _ = wait(futures, timeout=BUDGET_SECONDS)
    # don't wait for stragglers: the digest goes out without them
    pool.shutdown(wait=False, cancel_futures=True)
    late = {"kind": "error", "score": None, "text": "",
            "error": f"not finished within the {BUDGET_SECONDS // 60}-minute budget"}
    return {j["url"]: (f.result() if f in done else late) for f, j in futures.items()}


def filename(job):
    stem = f"{job.get('company', '')} - {job.get('title', '')}"
    stem = re.sub(r"[^\w .&()+-]+", " ", stem)
    return re.sub(r"\s+", " ", stem).strip()[:90] + ".md"


def status_line(result, fname):
    """The line under each APPLY FIRST role in the digest."""
    if result["kind"] == "resume":
        return f"Resume attached: {fname}"
    if result["kind"] == "audit":
        score = f" {result['score']}/100," if result.get("score") is not None else ""
        return f"Below the 90 gate:{score} audit attached: {fname}"
    if result["kind"] == "error":
        return f"Resume not generated ({result['error']}) - paste the JD into your Project"
    return f"Generator output attached: {fname}"
