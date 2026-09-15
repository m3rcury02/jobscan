# Remaining referral companies

Generated 2026-09-15, corrected 2026-09-16 after live verification of the "group A" platform
guesses. **The platform column in the original version was a guess, not data** - of the 12
group-A companies actually probed this session, only 1 guess (Testsigma) was right. Treat
every unverified row below the same way: a starting point for a real check, not something to
re-slug on faith. `careers_urls.txt` records the checks already done that way.

As of 2026-09-16: 79 of 245 referral companies are wired into `companies.csv` and verified live
(see git log / `companies.csv`). This file tracks the rest.

## Covered since 2026-09-15 (moved out of this list)

Carelon, Dell Technologies, Goldman Sachs, Texas Instruments, Newspace (Keka adapter fixed for
its 2026 template), Testsigma (was mistagged `lever`; real board is `testsigma.keka.com`).

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

  BNP Paribas              bespoke
  Blackhawk                bespoke
  Brillio                  bespoke
  Eli Lilly                bespoke
  Flipkart                 bespoke (turbohire.co renders empty even after 9s - skip)
  GlobalLogic              bespoke
  HashedIn                 bespoke
  KPIT                     bespoke
  Loginsoft                bespoke
  Manipal Hospitals        bespoke
  Nextwealth               bespoke
  Polestar Analytics       bespoke
  STL Digital              bespoke
  Siemens                  bespoke (covered - firecrawl)
  Tesco                    bespoke (covered - firecrawl)
  UnitedLayer              bespoke
  Volvo                    bespoke

Plus Schneider Electric, Social Panga, Societe Generale (group A "own").

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
