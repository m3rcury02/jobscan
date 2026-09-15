# Remaining referral companies

Generated 2026-09-15, corrected 2026-09-16 after live verification of the "group A" platform
guesses. **The platform column in the original version was a guess, not data** - of the 12
group-A companies actually probed this session, only 1 guess (Testsigma) was right. Treat
every unverified row below the same way: a starting point for a real check, not something to
re-slug on faith. `careers_urls.txt` records the checks already done that way.

As of 2026-09-16: 80 of 245 referral companies are wired into `companies.csv` and verified live
(see git log / `companies.csv`). This file tracks the rest.

## Covered since 2026-09-15 (moved out of this list)

Carelon, Dell Technologies, Goldman Sachs, Texas Instruments, Newspace (Keka adapter fixed for
its 2026 template), Testsigma (was mistagged `lever`; real board is `testsigma.keka.com`),
Eli Lilly (firecrawl row - `careers.lilly.com/us/en/india`, Phenom-powered, verified parses
cleanly: see `tests/fx_lilly.md`).

## Tier 3 (firecrawl) reality check - a parser limitation, not a config problem

Tried 10 candidate careers pages for group B/"own" companies this session. Only Eli Lilly
parsed cleanly. The other 9 failures share one root cause: `parse_markdown_jobs` (in
`jobscan.py`) extracts title/location by treating the **markdown link text** as the whole
card - it assumes the title is the first line inside `[...]（url)`. That holds for Tesco,
Siemens, and Goldman (the existing fixtures) and for Eli Lilly's Phenom cards, but not for:

- **Brillio, KPIT**: title is a heading *outside* the link; the link text is just "Apply".
- **GlobalLogic**: the link text puts location+work-model *before* the title
  (`[BangaloreIndiaHybrid\n**MySQL Database Administrator**](url)`), so the parser reads the
  location as the title and the real title never matches `TITLE_KEEP`.
- **Nextwealth**: no job listing on the page at all (marketing copy only) - not a parser issue,
  there's genuinely nothing to scrape.
- **Volvo**: `volvogroup.com/en/careers.html` is a landing page; the real listing is at
  `jobs.volvogroup.com` (untried).
- **Societe Generale**: `careers.societegenerale.com` is France-only in French; the India/English
  listing (linked from that page as "à l'étranger") needs its own URL, untried.
- **BNP Paribas**: the IT/Tech/Data page shows only 4-5 curated highlights, not the full
  listing; the real listing needs the "See all offers" URL with query params, untried.

**The fix that unlocks this whole tier**: pass `jsonOptions` with a schema
(`title`/`location`/`url`/`posted`) to `firecrawl_scrape`/`fetch_firecrawl` instead of relying
on markdown-link-text heuristics. That's shape-agnostic - it would have worked on Brillio, KPIT,
and GlobalLogic as-is. This is a `fetch_firecrawl` change (new code), not something achievable
by picking better URLs, and wasn't attempted this session. Worth doing before adding more
firecrawl rows one at a time.

**Wells Fargo and Eli Lilly are both Phenom** (`cdn.phenompeople.com` in the page assets) - if
the Phenom bucket gets revisited, Lilly's markdown shape is the one that already parses.

## A1. Verified this session - real platform found, not yet added

None of these have a working adapter today; each needs either new adapter code or a firecrawl
row.

| Company | Real platform (verified 2026-09-16) | Note |
|---|---|---|
| Ather | bespoke (`careers.atherenergy.com/job/<hash>`) | not lever, not ashby - own portal |
| Apexon | bespoke (`apexon.com/career-job-detail/?id=…&jobid=…`) | not workday |
| Wells Fargo | **Phenom** (`wellsfargojobs.com`) | not workday - `fetch_workday` will never work here |
| Smiths Detection | bespoke (`smithsdetection.com/careers/job-search-results/`) | not workday |
| Syngene | **SuccessFactors Career Site Builder** (`career10.successfactors.com`) | different template than `fetch_successfactors` parses (that adapter targets the "Recruiting Marketing" template, e.g. CommScope/Swiss Re) - needs adapter work, not a reslug |
| PhonePe | bespoke in-house (`phonepe.com/careers/job-openings/`) | not lever |
| FinBox | Reczee (`app.reczee.com`) | unsupported ATS, low priority |
| Snapmint | openings.co (`careers.snapmint.com`, powered by `impl.openings.co`) | unsupported ATS |
| GIVA | Dover (`app.dover.com/apply/GIVA`) | unsupported ATS |
| Zemoso | **Workable** (`apply.workable.com/zemoso-technologies`) | adapter exists (`fetch_workable`) but 0 open roles right now - add the row anyway, it'll pick up postings when they appear |
| NTT DATA | same Workday tenant (`nttglobaldatacenters`) as the already-covered NTT Global Data Centers row | the global careers page doesn't expose a separate NTT DATA site within that tenant; no separate coverage found |
| Infineon | Eightfold, confirmed 403 again | genuinely blocked, not a guessed-slug problem |
| Qualcomm | Eightfold, confirmed 403 again | genuinely blocked |
| Epsilon | SmartRecruiters tenant `Epsilon1` exists but is stale (1 US posting from 2020) | not worth adding |
| NAVEX | not Workday - earlier "workday" hit was a CSS class name (`.rich-text--workday`), not an ATS link | real platform not found yet |

## A2. Unverified - guess only, needs the same live check before adding

  7-Eleven*                Analyttica                Anheuser-Busch
  Ati Motors                Blissclub(blocked)        Capillary Technologies
  ClearTax                  Deutsche Bank             Fastenal
  Gale                      HiLabs(blocked)           Impact Analytics
  Indegene                  Indus DC Ventures         Lowe's
  Morgan Stanley            MrMed                      Myntra(blocked: login wall)
  Netcore Unbxd              Nutanix                    Nykaa
  Polymerize                Publicis Groupe            R360
  Schneider Electric        Social Panga               Societe Generale
  SoundHound                 Standard Chartered         StatusNeo
  Storylane                  Think Design               Transak
  Tricon Infotech            Uni Cards                  Urban Company
  Xflow                       Zepto(blocked: no tech roles)
  Akamai                      Dell Technologies(covered) Texas Instruments(covered)
  Carelon(covered)            TEKsystems

*7-Eleven: a Workday tenant (`7eleven`, `wd3`) exists and is real, but every listing found in
it is US/Australia retail store operations (Ipswich, Cairns, Geraldton) - no evidence of an
India tech site within that tenant. Needs more digging before treating it as a hit.

Checked-and-404 this session (guessed slug, live probe failed - do not reuse these tokens):
`ashby:transak`, `ashby:xflow`, `ashby:getxflow`, `ashby:gale`, `eightfold:deutschebank`,
`eightfold:db`.

## B. Own careers site, alert varies (17) - Tier 3 firecrawl candidates

  BNP Paribas              bespoke (tried - curated highlights only, needs "all offers" URL)
  Blackhawk                bespoke (real ATS is iCIMS - careers-blackhawknetwork.icims.com)
  Brillio                  bespoke (tried - fails parser, see note above)
  Flipkart                 bespoke (turbohire.co renders empty even after 9s - skip)
  GlobalLogic              bespoke (tried - fails parser, see note above)
  HashedIn                 bespoke
  KPIT                     bespoke (tried - fails parser, see note above)
  Loginsoft                bespoke
  Manipal Hospitals        bespoke
  Nextwealth               bespoke (tried - no job listing on the page)
  Polestar Analytics       bespoke (real URL is /career not /careers)
  STL Digital              bespoke
  Siemens                  bespoke (covered - firecrawl)
  Tesco                    bespoke (covered - firecrawl)
  UnitedLayer              bespoke
  Volvo                    bespoke (tried - landing page only, real listing at jobs.volvogroup.com)

Plus Schneider Electric (403 to plain requests, untried via firecrawl), Social Panga (untried),
Societe Generale (tried - France/French-only listing, India/English page untried).

## C. No findable job board — LinkedIn alert or referral contact only (95)

  ADA                       American Express          Andpayment              
  Apple                     Aster                     Atlassian               
  Aukera                    BT Group                  Blackbox                
  Bluehill                  Bravura Tech              Brick&Bolt              
  CadreSports               Capgemini                 Catechy Solutions       
  Cessna                    Chaarana Labs             CliniExperts            
  Cognizant                 Creysto                   Dassault Systemes       
  Deloitte                  Diligent                  Dohful                  
  EY                        Earnifi                   Egis                    
  Espirito                  Euphoric Thought          Fashion UK              
  Federal Bank              GoComet                   Google                  
  Grant Thornton            HCLTech                   HDFC                    
  Healjour                  IBM                       IML                     
  Infosys                   Intueri                   Iylon                   
  KNCC                      KPMG                      Kearney                 
  Kepler Aerospace          L&T                       LTIMindtree             
  Les Baskets               Limitless                 Lingopanda              
  Madhan Mohan              Mazle.ai                  Mewesalus Care          
  Microsoft                 Mining Grid               Monk Studios            
  NI                        NPF                       NSRCEL IIMB             
  NST                       Netchex                   Nirad                   
  OSBI                      Opptra                    Pazy                    
  Pine Labs                 Pragma                    Primetrace              
  PwC                       Quantacus AI              Quantum QMCO            
  RBIH                      RP & Co                   Resyynth Agritech       
  Rocket                    Rootpay                   Roots India             
  SKS Advisor               STG Labs                  Sechpoint               
  Skilign                   Svarapps                  SwiffyLabs              
  TCL                       TCS                       Tech Mahindra           
  TekChant                  TinkerKraft               Triloma                 
  Triplespeed               UBL                       Userfacet               
  Wipro                     ZS                      
