# jobscan

Polls company ATS boards every two hours, filters to India-based backend / full-stack / AI
roles at 0-5 years, scores each against your stack, and emails a ranked digest.

310 boards across 14 adapters. Roles at companies where you have a referral bypass the
service-firm filter and are tagged *REFERRAL* in the digest.

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

6. Push. The workflow runs every two hours. Trigger a manual run from the Actions tab
   to confirm the email lands.

   Cadence is every two hours rather than hourly because the repo is private, so Actions
   bills against the 2000 minute free tier. A ~3 billed minute run every hour needs about
   2160 of them. Make the repo public for unlimited minutes if you want hourly.

## Modes

Actions > jobscan > Run workflow > mode:

| mode | what it does |
|---|---|
| `scan` | the default, and what the cron runs. Roles from the last 2 days. |
| `backlog` | repairs seen.json, then reports every open role up to 90 days old. Run once after adding boards. |
| `discover` | reads `wanted.txt`, finds each company's board, writes verified ones to `companies.csv`. |
| `diagnose` | probes every board and reports why each failure happened. Run monthly. |
| `prune` | comments out every row `diagnose` found returning 404. |
| `dry-run` | prints the digest, sends nothing. |

## Files

- `companies.csv` - your target board list. The only file you edit regularly.
- `wanted.txt` - companies to find boards for. `discover` mode reads this.
- `careers_urls.txt` - hand-supplied careers URLs, with the outcome of each recorded.
- `seen.json` - URLs already reported. Prevents repeat digests. Committed by CI.
- `pipeline.csv` - append-only record of everything found, with scores.
- `fetch_errors.log` - boards that failed, with reason.
- `dead_rows.txt` - written by `diagnose`, consumed by `prune`.
- `discover.py` - board discovery. Separate from the scanner; only runs in `discover` mode.

## The Referral column

`COMPANY_DROP` keeps mass-market service firms out of the digest. A referral inverts that
tradeoff - a role someone can walk you into is worth seeing whoever posted it - so a row
with `Referral = yes` skips the check entirely and its roles are tagged `*REFERRAL*` in
the digest and in `pipeline.csv`.

Without it, adding Accenture, Infosys, TCS, Wipro, Cognizant, Capgemini, HCLTech,
LTIMindtree or Tech Mahindra achieves nothing: every job they return is discarded.

## seen.json is "already emailed", nothing else

This file decides what you never see again, so the one rule that matters: only ever add
a URL to it that was actually sent to you. An earlier version also added anything older
than the freshness window, which silently buried 547 open roles - the digest showed 16 a
day while hundreds sat hidden.

`python jobscan.py --repair-seen` rebuilds it from `pipeline.csv`, which is the real
record of what was sent. Run it if the digest ever goes suspiciously quiet.

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

## Adapters

| ATS value | notes |
|---|---|
| `greenhouse` `lever` `ashby` `smartrecruiters` `workable` | plain GET, public JSON |
| `workday` | POST. Token = site, Tenant = `<name>.wdN`. See below. |
| `oracle` | Token = site number (often `CX_1`), Tenant = full oraclecloud host |
| `keka` | Token = subdomain. Two GETs to learn the tenant id, one to read jobs. |
| `eightfold` | Token = subdomain, Tenant = the `domain=` param. Caps pages at 10. |
| `successfactors` | Token = host, e.g. `jobs.ametek.com`. Two page templates exist; both parsed. |
| `pinpoint` | Token = subdomain. Public JSON at `/postings.json`. No posting dates. |
| `amazon` | no token needed. Filters on `normalized_country_code`, not the fuzzy loc_query. |
| `custom` `agency` | plain GET plus text heuristics. See below. |
| `firecrawl` | Token = full search URL, Tenant = waitFor ms. Real browser via Firecrawl; once a day. See below. |

Naukri is deliberately absent: it needs a headless browser and its listings skew heavily
toward IT services bulk hiring.

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

Workday matters because it is where the India engineering centres of large
global companies sit. It is also the fiddliest adapter, for two reasons.

**The shard in the careers URL is not always the shard the API lives on.**
Omnissa's careers URL says `wd5`; its tenant is on `wd501`. 7-Eleven's says
`wd5`; it is on `wd3`. `discover.py` sweeps thirteen shards for this reason.

**Read the status code, not your intuition.** With one valid payload held
constant:

```
cisco.wd5 + Cisco_Careers -> 200      cisco.wd5 + BOGUS -> 404
cisco.wd1 + Cisco_Careers -> 422      cisco.wd1 + BOGUS -> 422
```

Since that body returns 200, a 422 is not schema rejection. 422 means the
tenant is not on that shard - try the next one. 404 means it is, and only the
site name is wrong - start guessing site names. Site names are arbitrary
strings: Cardinal Health's is `Ext`, HPE's is `Jobsathpe`, Citi's is `2`.

Dates arrive relative ("Posted 3 Days Ago", "Posted Today", "Posted 30+ Days
Ago") and are converted to real dates, so the recency window works normally.

The adapter sends `searchText: "India"` and pages up to `WORKDAY_MAX` (600)
results per board. Some tenants report the real total on page one and 0 on
every page after, so the loop keeps the largest total it has seen rather than
trusting each page - without that, Accenture truncated at 40 of 600.

## Firecrawl (ATS type `firecrawl`)

For careers pages that only render in a browser: Next.js sites, Avature, and
anything where `custom` finds zero roles. Add the repo secret
`FIRECRAWL_API_KEY`. Rows without it log an error and are skipped.

```
Goldman Sachs,firecrawl,https://higher.gs.com/results?LOCATION=Bengaluru&search=engineer,8000,,yes
```

It requests markdown (1 credit a page) and reads `[title](url)` links, so
every role keeps its real URL, unlike `custom`'s `page#slug`. Dates are rarely
present, so these roles fall back to first-sighting dating like `custom`.

**Cost.** Firecrawl rows run only on the UTC hours in `FIRECRAWL_HOURS_UTC`
(default `2`, i.e. 07:30 IST, once a day) during scheduled scans, and on every
manual mode. Five rows use about 150 credits a month. Putting a row on every
2-hour scan would use 12x that.

**What it will not fix.** Workday serves headless browsers a fake outage page;
use the `workday` adapter. Pages behind a candidate login (some Darwinbox
tenants) have nothing public to read. A slug that does not exist is still a
slug that does not exist: check the page text before blaming rendering.

Parser tests: `python -m pytest tests/` (fixtures are real Firecrawl output).
