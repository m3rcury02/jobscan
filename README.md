# jobscan

Polls company ATS boards every weekday, filters to India-based backend / full-stack / AI roles
at 0-5 years, scores each against your stack, and emails a ranked digest.

No API keys for the job boards. All endpoints are public.

## Setup (15 minutes)

1. Create a private GitHub repo and push these files.

2. **Gmail app password.** Google Account > Security > 2-Step Verification > App passwords.
   Generate one for "Mail".

3. **Repo secrets.** Settings > Secrets and variables > Actions > New repository secret:
   - `SMTP_USER` = gunal.works@gmail.com
   - `SMTP_PASS` = the 16-character app password
   - `SMTP_TO` = gunal.works@gmail.com

4. **Fill `companies.csv`.** This is the part that decides whether the whole thing is
   useful. The seed rows are examples, not a target list. Replace them.

   To find a company's ATS, open their careers page and look at where it redirects:

   | Careers URL contains | ATS value | Token is |
   |---|---|---|
   | `job-boards.greenhouse.io/xyz` | `greenhouse` | `xyz` |
   | `jobs.lever.co/xyz` | `lever` | `xyz` |
   | `jobs.ashbyhq.com/xyz` | `ashby` | `xyz` |
   | `jobs.smartrecruiters.com/xyz` | `smartrecruiters` | `xyz` |
   | `apply.workable.com/xyz` | `workable` | `xyz` |
   | `*.oraclecloud.com/hcmUI/...` | `oracle` | `CX_1` + set Tenant |

   Faster route: Google `site:job-boards.greenhouse.io "company name"`.

5. **Test locally** before trusting the cron:
   ```
   pip install requests
   python jobscan.py --dry-run
   ```
   Check `fetch_errors.log` for any board returning 404 (wrong token) or 403.

6. Push. The workflow runs 07:30 IST Mon-Fri. Trigger a manual run from the Actions tab
   to confirm the email lands.

## Files

- `companies.csv` - your target board list. The only file you edit regularly.
- `seen.json` - URLs already reported. Prevents repeat digests. Committed by CI.
- `pipeline.csv` - append-only record of everything found, with scores.
- `fetch_errors.log` - boards that failed, with reason.

## Tuning

Edit the constants at the top of `jobscan.py`:

- `CORE_SKILLS` / `SECONDARY_SKILLS` / `AI_SKILLS` - weighted keyword banks. Add a skill
  as you gain it. Weights are 1-3.
- `MAX_YOE` - currently 5. Roles asking for more are dropped.
- `TITLE_KEEP` / `TITLE_DROP` - if you see junk in Section B, add the offending word to
  `TITLE_DROP` rather than lowering the score threshold.
- `COMPANY_DROP` - service firms and staffing agencies.

Scoring is 50% weighted skill overlap, 30% years fit, 20% role type. It is deterministic
and cheap. If it ever disagrees with your judgement, the keyword bank is wrong, not the
formula.

## Workflow

The digest is triage, not a decision. For anything in Section A:

1. Open the URL, read the actual JD.
2. Paste it into your resume generator project, generate the tailored resume.
3. Apply by hand.
4. Set `Status` and `AppliedDate` in `pipeline.csv`.

## Adding Naukri or Workday

Both need a headless browser or a POST body, so they do not fit the plain-GET adapter
pattern. Add them only if a target company is reachable no other way. Naukri's backend
listings skew heavily toward IT services bulk hiring.
