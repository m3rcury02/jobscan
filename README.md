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

## Companies with no ATS (custom careers pages)

Add them with `ATS = custom` and the full careers URL as the `Token`:

```
MoveInSync,custom,https://moveinsync.com/join-us,,
```

The adapter does a plain GET, then keyword-matches title-shaped lines in the
page text. No per-site selectors, nothing to maintain when a site is restyled.

Two consequences worth understanding:

**No posting date.** These pages don't publish one, so the 2-day window cannot
apply. Instead they are dated by first sighting: a role is reported the first
scan it appears in, then recorded in `seen.json` and never shown again. Running
daily makes this equivalent to a 1-day window.

**No job description.** Scoring them against your stack would be meaningless, so
they go to digest Section D unscored. Open the page to judge.

**JavaScript-rendered pages return nothing.** The plain GET sees no jobs and the
row quietly finds zero roles. If you suspect this, open the careers URL with JS
disabled - if the jobs vanish, the page needs a headless browser and is not
worth the maintenance.

### Finding whether a company actually has an ATS

Most "custom" careers pages are an ATS behind a vanity domain. Before adding a
`custom` row, check:

1. Open the careers page and click Apply. Watch where it redirects.
2. View source and search for: greenhouse, lever, ashby, workable,
   smartrecruiters, keka, darwinbox, zohorecruit, myworkdayjobs, icims.
3. Google `site:job-boards.greenhouse.io "company name"`, then the same for
   jobs.lever.co and jobs.ashbyhq.com.

If any of those hit, use the real ATS adapter instead. You get posting dates,
full descriptions and proper scoring - all three of which `custom` loses.

## Recruitment consultancies (ATS type `agency`)

Six staffing and recruitment boards are included with `ATS = agency`. They are
scraped like `custom` rows but routed to digest Section E and never scored,
because their postings do not name the hiring company.

**Why they are separated rather than excluded.** Agencies genuinely surface
roles that never reach a public board, and some place well at product
companies. But an unnamed JD cannot be researched, cannot be matched against
your stack, and cannot be used to tailor a resume - so scoring it would be
fiction.

**The rule that matters.** Before applying through an agency, search a
distinctive phrase from the JD to work out who the employer is. If that company
has a direct board in this file, apply there instead. Recruitment agencies
claim candidate ownership for a period after submitting you - typically six to
twelve months - and a company faced with a fee claim will often drop the
candidate rather than pay. Applying through two channels is the fastest way to
lose a role you would otherwise have got.

**Also watch for:** the same role posted by three agencies at once, "immediate
joiners only" listings that ignore your notice period, and firms that submit
your resume to clients without asking first. Ask before you send anything.

## Workday (ATS type `workday`)

Workday's job list is a POST endpoint rather than a GET, so it needs its own
adapter. Read both values off any Workday careers URL:

```
https://cisco.wd5.myworkdayjobs.com/en-US/Cisco_Careers/job/...
             ^ Tenant = cisco.wd5       ^ Token = Cisco_Careers
```

Sixteen tenants are pre-configured. Workday matters because it is where the
India engineering centres of large global companies sit - Adobe, Intuit, Visa,
Mastercard, Walmart Global Tech, Target India, JPMorgan, Goldman. Adding one is
two minutes of reading a URL.

Dates arrive relative ("Posted 3 Days Ago", "Posted Today", "Posted 30+ Days
Ago") and are converted to real dates, so the recency window works normally.

The adapter sends `searchText: "India"` and pages up to 200 results per board.
Very large boards may truncate; narrow by adding the same company twice with
different sites if that becomes a problem.
