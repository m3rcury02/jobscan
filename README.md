# jobscan

Polls company ATS boards every hour, filters to India-based backend / full-stack / AI /
cloud roles whose stated experience band includes yours (`MY_YOE`, default 2), fetches the
full job description, scores it against your stack, and emails a digest that opens with
the handful of roles to apply to first.

About 350 boards across 24 adapters, including 22 IT services firms (TCS, Infosys, Wipro,
HCLTech, LTIMindtree, Persistent, Coforge ...). Roles at companies where you have a referral
are tagged *REFERRAL* and ranked up.

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

6. Push, then trigger a manual run from the Actions tab to confirm the email lands.

7. **Set up an external hourly trigger** (see "Hourly runs" below). The workflow's own
   cron is only a backup: GitHub drops most of its runs.

## Hourly runs

GitHub's scheduler drops most scheduled runs for this repo. `0 */2 * * *` ran 3-5 times
a day instead of 12 (2026-09-27 to 10-05); moving to `17 * * * *` did not help: 2 runs in
the 14 hours after the change (2026-10-06/07). Every manual run starts at once, so an
outside scheduler that triggers the workflow is what makes it hourly. 5 minutes, free:

1. **Token.** GitHub > Settings > Developer settings > Personal access tokens >
   Fine-grained tokens > Generate new token. Repository access: only `jobscan`.
   Permissions: Actions = Read and write. Nothing else. Expiry: up to a year (diarise it).
2. **Scheduler.** At cron-job.org (free), create a cron job:
   - URL: `https://api.github.com/repos/m3rcury02/jobscan/actions/workflows/jobscan.yml/dispatches`
   - Schedule: every hour, at minute 17
   - Advanced > Request method: `POST`
   - Headers: `Authorization: Bearer <the token>`, `Accept: application/vnd.github+json`,
     `X-GitHub-Api-Version: 2022-11-28`
   - Request body: `{"ref":"main","inputs":{"mode":"scan","max_age":"2"}}`
   - It should report HTTP 204. A run then appears in the Actions tab within seconds.
3. Leave the cron in the workflow as a backup. When both fire in the same hour, the
   `concurrency` group queues the second run, which finds nothing new and sends nothing.

Hourly needs a public repo (unlimited minutes); on a private one, ~9 minute runs every
hour would need far more than the 2,000 free minutes.

## After updating to the JD-enrichment version: run `backlog` once

Before 2026-09-25, Workday and Oracle roles were scored on their title alone (their list
APIs return no description), so almost all of them landed under 40 and were collapsed into
the "Section C: N filtered out" count - 1,041 of 1,310 roles at referral companies were
never shown. Every SmartRecruiters link was also a 404. `backlog` mode now releases exactly
those rows from `seen.json`, fetches their full JDs and re-judges them. Run it once.

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
| `applied` | records an application: paste the posting URL, pick a status. No checkout needed - it runs in Actions. |

## Files

- `companies.csv` - your target board list. The only file you edit regularly.
- `wanted.txt` - companies to find boards for. `discover` mode reads this.
- `careers_urls.txt` - hand-supplied careers URLs, with the outcome of each recorded.
- `seen.json` - URLs already reported. Prevents repeat digests. Committed by CI.
- `applied.csv` - your applications. Written by `applied` mode or by hand; the scanner only
  reads it (follow-up nudges, and a flag on new roles at companies you applied to recently).
- `pipeline.csv` - one row per role found, with years band, score and digest section. The
  scanner owns it; `Status`/`AppliedDate` are the only columns it will not overwrite.
- `fetch_errors.log` - boards that failed, with reason.
- `board_health.json` - last good poll per board; drives BOARDS DOWN. Committed by CI.
- `daily_sent.txt` - IST date of the last once-a-day email (follow-ups, boards down).
- `dead_rows.txt` - written by `diagnose`, consumed by `prune`.
- `discover.py` - board discovery. Separate from the scanner; only runs in `discover` mode.
- `resumes.py` - tailored resumes for APPLY FIRST roles. Off until its secrets exist.

## The Referral column

A row with `Referral = yes` has its roles tagged `*REFERRAL*` in the digest and in
`pipeline.csv`, ranked above a slightly better match without one, and given the
referral-first instruction. It also skips `COMPANY_DROP`, which since 2026-10-01 holds only
staffing agencies: a role someone can walk you into is worth seeing whoever posted it.

## Boards down

`board_health.json` records each board's last good poll and how many roles it returned.
A board that has failed for 6 hours, or come back with 0 roles after having 5 or more,
is listed under **BOARDS DOWN** at the top of the digest, referral companies first, and
gets one email a day even when nothing new turned up. Before this, TCS returned nothing
for five days (its careers site moved to a new API) and five RippleHire boards failed on
a third of runs, and the only sign was a count in the digest footer. If a board is still
listed after you have checked it really has no openings, comment its row out.

## seen.json is "already emailed", nothing else

This file decides what you never see again, so the one rule that matters: only ever add
a URL to it that was actually sent to you. An earlier version also added anything older
than the freshness window, which silently buried 547 open roles - the digest showed 16 a
day while hundreds sat hidden.

`python jobscan.py --repair-seen` rebuilds it from `pipeline.csv`, which is the real
record of what was sent. Run it if the digest ever goes suspiciously quiet.
It also leaves out rows that were found but never really shown - the title-scored
Workday/Oracle rows and the 404ing SmartRecruiters links from before 2026-09-25 - so
the `backlog` run that follows re-checks them with their full JD.

## Tuning

Edit the constants at the top of `jobscan.py`:

- `MY_YOE` - your years of experience (2). A role is kept when the band its JD states
  includes this ("0-2", "1-3", "2-4", "2+"), marked *stretch* when it asks for up to
  `MY_YOE + YOE_STRETCH` (3), and dropped above that. A band you are past ("0-1",
  "freshers only") is kept but ranked lower. Bump it as you gain experience.
- `HAS_MASTERS` - "Bachelor's + 7 years OR Master's + 4 years" is read on the Bachelor's
  route unless this is True.
- `CORE_SKILLS` / `SECONDARY_SKILLS` / `AI_SKILLS` - weighted keyword banks, matched as
  whole words. Add a skill as you gain it (and remove it from `GAP_SKILLS`). Weights 1-3.
- `TITLE_KEEP` / `TITLE_DROP` - if you see junk, add the offending word to `TITLE_DROP`
  rather than raising the score threshold.
- `COMPANY_DROP` - staffing agencies.
- `MORE_PER_COMPANY` - roles listed per company in the longer digest sections before a
  "+N more at X" line. Service firms post in bulk; this keeps one of them from burying the
  rest. Every role is in `pipeline.csv` regardless.
- `APPLY_FIRST_MAX` / `PER_COMPANY_CAP` / `REFERRAL_BONUS` - the shape of the top list.

### How a role is judged

1. Cheap filters on the list data: new, not a staffing agency, engineering title, India,
   posted within `--max-age` days.
2. One detail call per survivor fetches the full JD (Workday, SmartRecruiters, Oracle,
   Eightfold; the other boards include it already). A failed fetch is logged and the role
   is shown as title-only, never scored on nothing.
3. Years: a band in the title wins ("Exp: 4-8 Yrs"); otherwise the highest minimum across
   the required lines - "3+ years of development, 2+ of design" asks for 3. Preferred /
   nice-to-have lines, Master's/PhD routes and company history ("for 40 years") are
   ignored. Graduation-year-gated roles ("2026 batch") are dropped. Junior phrasings are
   read as bands: "up to 2 years" and "less than 2 years" as 0-2 (not 2+), "Freshers to
   2 years", "Min 0 Max 2", "Experience Required: 0-2 Yrs", "6 months to 2 years",
   "0-24 months".
4. Seniority: "Senior", "Sr", "III" and "Staff Engineer" titles are kept only when the JD
   states a band you are in or one year short of. Indian product companies do post
   "Senior (2-4 yrs)", and Altimetrik's "Staff Engineer" is 2-5; without that evidence a
   senior title reads as 4-6+ years. Fresher / trainee / graduate / new-grad titles work
   the other way: kept only when the JD's band includes your years ("Trainee Software
   Engineer, 0-2 yrs"); the rest are campus programmes you have aged out of.
5. Score: 50% weighted skill overlap, 30% years fit (in-band 30, unstated 18, below 15,
   stretch 12), 20% role type. Deterministic and cheap. If it disagrees with your
   judgement, the keyword bank is wrong, not the formula.

## Tailored resumes for APPLY FIRST roles

Each APPLY FIRST role that has a job description gets your resume generator run on it, and
the result is attached to the digest as a markdown file: a one-page resume when your
eligibility gate scores 90 or above, otherwise the gate's audit (score, failed checks, and
the minimum points that would clear 90). The digest line under the role says which. It is
the same as pasting the JD into your claude.ai Project, which cannot be called from a script.

**Setup.** Repo Settings > Secrets and variables > Actions:

1. `RESUME_PACKET` - the full resume source packet (markdown).
2. `RESUME_INSTRUCTIONS` - the Project instructions, eligibility gate included.
3. One way to reach Claude:
   - `CLAUDE_CODE_OAUTH_TOKEN` - your Claude subscription. Run `claude setup-token` on your
     own machine (Claude Code installed, signed in) and paste the token it prints. No extra
     cost; it draws on the same usage limits as your interactive Claude use. The run installs
     the Claude Code CLI only when this route is in use.
   - or `ANTHROPIC_API_KEY` - the Anthropic API, paid per use (console.anthropic.com).
     About $0.10-0.20 a resume on Claude Opus 5.5 at the default effort. Wins if both are set.

Optional repository variables (not secrets): `RESUME_MODEL` (API default `claude-opus-5-5`;
the CLI uses your plan's default), `RESUME_EFFORT` (default `high`), `RESUME_MAX` (per run,
default 8). Resumes are only generated when the SMTP secrets are set, since they travel by
email.

**What it costs in time.** Measured: 33 s for an audit, about 2.5 min for a full resume.
Up to 8 per run (`RESUME_MAX`), 3 at a time, within a 15-minute budget; anything still
running then is marked "not generated" and the digest goes out without it.

**Privacy.** The packet, the instructions and every resume carry your phone number and email,
and this repo and its Actions logs are public. Keep them in secrets, never in a file here.
The scanner writes nothing to the repo and prints only counts to the log; resumes travel by
email only. Dry runs never generate (they would print to the public log).

**Read before you send.** These are drafts. A real run on a Java role produced a 518-word
resume that followed the format and left unsupported JD keywords out, but also merged two
unrelated facts into one bullet. Roles outside Bangalore lose 5 gate points unless the
packet says whether you will relocate.

## Workflow

The digest opens with **APPLY FIRST**: at most 8 roles, at most 2 per company, ranked by
fit score plus a referral bonus plus freshness. Each one carries its years band, the
skills from your stack the JD asks for (mirror those in the tailored resume - only ones
you have), what else the JD wants, and the next step.

1. Referral company? Send the link to your referrer and let them submit you *before*
   you apply. An existing application usually blocks or voids the referral.
2. Otherwise open the URL, read the JD, generate the tailored resume, apply by hand -
   ideally within 48 hours of posting.
3. Record it: Actions > jobscan > Run workflow > mode `applied`, paste the URL, pick a
   status (`referred` if a referrer submitted you). Update the status the same way.
4. The first digest at or after 07:30 IST each day lists applications 7-21 days old
   that still read `applied` or `referred`, with the follow-up to send.
   (`daily_sent.txt` records the day it went out.) New roles at a company you applied to in the
   last 30 days are flagged and demoted: one strong application per company beats five.

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
| `phenom` | Token = the site's search-results URL. Reads refNum/pageId from the page, then pages `/widgets`. Real dates. |
| `sfcsb` | SuccessFactors Career Site Builder (newer template). Token = host; Tenant = optional `field=value` facet. |
| `infosys` `tcs` `capgemini` | single-company APIs. TCS: `/candidate/next` API (moved 2026-10-01), XSRF token from the session call; no dates, so all ~1,800 India roles are read. |
| `zwayam` | Token = careers base URL, Tenant = Zwayam company id (COMPANYID in the site's main.js). |
| `ripplehire` | Token = subdomain, Tenant = careers token, optionally `\|geo=India` (LTIMindtree only; other tenants return 0 with it). No dates, so every role is read. Sessions without a JSESSIONID get empty replies and are renewed. |
| `avature` | Token = the portal's SearchJobs URL (keywords may sit in the path). Tenant = location to assume for "Multiple Locations". |
| `selectminds` | Oracle SelectMinds. Token = site root. No dates. |
| `techmahindra` | ASP.NET form postbacks. Title, band and skills only; the link names the Job Reference ID to search. |
| `jibe` | iCIMS Jibe sites. Token = host, Tenant = location. Unit (e.g. Epsilon) appended to the title. |
| `rss` | A site's own job feed: RSS `<item>` or Indeed-style `<job>`. Token = feed URL, Tenant = country to keep. |
| `freshteam` | Token = subdomain. Reads the public widget feed (`/hire/widgets/jobs.json`): every published job with JD, branch and date. The `/jobs` page `custom` used to scrape showed a fraction of them. `agency` rows on Freshteam use it too. |
| `pcsx` | Eightfold's newer career-site API. Token = host (`apply.careers.microsoft.com`), Tenant = the `domain=` param. Use it where `eightfold`'s `/api/apply/v2` returns 403 (Microsoft, Qualcomm, Infineon, Morgan Stanley). |

`successfactors` reads the real result count ("of 2,242") and sorts newest first. Before
2026-10-01 it read the page range as the total and stopped after 25 roles on every board.
`oracle` likewise sorts newest first and pages past 200.

Naukri is deliberately absent: it needs a headless browser and its listings skew heavily
toward IT services bulk hiring.

## IT services firms

Added 2026-10-01. None has a board on the ATS platforms `discover.py` probes, so each
source was read out of the careers site's own JavaScript: TCS iBegin, the Infosys careers
API, SuccessFactors CSB (Wipro, HCLTech), Zwayam (Persistent, Coforge, Cyient), RippleHire
(LTIMindtree, Mphasis, Altimetrik, Tata Technologies), Capgemini's job-search API, Oracle
HCM (Hexaware, Zensar, EXL, KPMG), SuccessFactors (Birlasoft, EY, Atos), Workday (NTT DATA,
DXC) and Phenom (Quest Global). Most publish the experience band as data, so the years
filter works on them even where the JD prose does not state it.

What to expect: bands at these firms are wide ("4-14 years") and often start above 3, so
the years filter drops a large share. TCS, LTIMindtree, Capgemini, EY and the RippleHire
boards publish no posting date; their first scan after being added reports every open role
once, then only new ones.

Also reached, in a second pass: Cognizant (its own XML job feed - the careers pages are
behind Cloudflare for browsers, the feed is not), Tech Mahindra (form postbacks), Deloitte
India (Avature), Virtusa (SelectMinds), Publicis Groupe incl. Sapient and Epsilon (iCIMS
Jibe), Unisys (Workday), UST (RippleHire), Brillio (Lever) and Mastek (SuccessFactors).
Tesco and Siemens moved off Firecrawl onto the Avature adapter, and Goldman Sachs onto
the Oracle adapter (higher.gs.com is a front end over its Oracle site `LateralHiring`), so
no row needs Firecrawl any more. Postman moved to Workday and Amplitude to Ashby in
2026-10.

Still not covered: EPAM India (Cloudflare WAF block even for a real browser, from any
datacenter IP), GlobalLogic (its job search call returns 403 to datacenter IPs, browser
included), Happiest Minds (Darwinbox behind a Cloudflare Turnstile check), and KPIT (needs
a real browser to pass a JavaScript check; Playwright on every hourly run adds a browser
install and a minute or more per run for one board). `remaining_companies.md` has the
full list of what is still uncovered and why.

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
they go to the digest's TITLE MATCH ONLY section unscored. Open the page to judge.

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
scraped like `custom` rows but routed to the digest's AGENCY section and never scored,
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
Example Co,firecrawl,https://careers.example.com/search?q=engineer&location=India,8000,,
```

No row uses it at present (the last, Goldman Sachs, moved to the `oracle` adapter).

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

Tests: `pip install pytest && python -m pytest tests/`. Firecrawl fixtures are real
output; the years-of-experience cases are lines lifted from live JDs.
