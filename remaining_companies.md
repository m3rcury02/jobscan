# Remaining referral companies

Rewritten 2026-10-06. The 2026-09-16 version listed 166 referral companies as uncovered;
28 of those are now in `companies.csv`, and several of its "genuinely blocked" verdicts
turned out to be wrong. Everything below was checked live on 2026-10-06 unless it says
otherwise. `careers_urls.txt` keeps the older per-URL outcomes.

The lesson from both rounds: "no findable board" usually meant "not on a platform
`discover.py` probes". The IT services firms (2026-10-01) and Microsoft, Qualcomm,
Infineon and Morgan Stanley (2026-10-06) all had a public JSON API behind their careers
site. Read the site's network calls before writing a company off.

## Covered since 2026-09-16

| Company | Adapter | Note |
|---|---|---|
| TCS, Infosys, Wipro, HCLTech, LTIMindtree, Capgemini, Cognizant, Tech Mahindra, EY, KPMG, Deloitte India | various | IT services pass, 2026-10-01 |
| Publicis Groupe, Brillio, NTT DATA, Carelon, Dell, Texas Instruments, Siemens, Tesco, Goldman Sachs | various | 2026-09-16 to 10-01 |
| Microsoft | `pcsx` | Eightfold PCSX API; 234 India roles |
| Qualcomm | `pcsx` | was "403, blocked"; the v2 API 403s, PCSX does not. 583 India roles |
| Infineon | `pcsx` | same; 155 India roles |
| Morgan Stanley | `pcsx` | was "tal.net, no adapter"; 113 India roles |
| PwC | `workday` | `pwc.wd3` / `Global_Experienced_Careers`; 88 India roles |
| Lowe's | `workday` | `lowes.wd5` / `LWS_External_CS`; 97 India roles |
| Volvo | `successfactors` | `jobs.volvogroup.com`; 37 India roles |
| ZS | `jibe` | `jobs.zs.com`; 49 India roles |
| Polymerize | `freshteam` | real tenant, 0 open roles today |

## Blocked: platform known, nothing public to read from a datacenter IP

| Company | Platform | What happens |
|---|---|---|
| Nykaa, Capillary Technologies, Urban Company, Myntra, Pine Labs, Porter India, Happiest Minds | Darwinbox (`*.darwinbox.in`) | Cloudflare Turnstile. Even Firecrawl's browser gets either nothing (Nykaa), a login wall (Urban Company, Myntra) or the page shell without the job list (Capillary). 3Pillar's `.com` tenant does render via Firecrawl, so this is per-tenant. |
| Zepto | Darwinbox | reachable, but lists only ops / FMCG / real-estate roles |
| Blissclub, HiLabs | Keka | tenant exists, public portal switched off |
| EPAM India | own site | Cloudflare WAF blocks datacenter IPs, browser included |
| GlobalLogic | own site | job search call 403s to datacenter IPs |
| KPIT | own site | JavaScript check needs a real browser |
| Wells Fargo, Nutanix, Schneider Electric, Akamai, Kearney, BNP Paribas | own sites | 403 to a plain request on 2026-10-06; untried in a browser |

## Not yet tried properly: public site exists, needs an adapter or a closer look

Ordered roughly by how much India engineering hiring each does.

| Company | Lead |
|---|---|
| Google | careers.google.com - no documented API; results page embeds data |
| Apple | jobs.apple.com - search API needs a CSRF token |
| IBM | ibm.com/careers search - a JSON search API exists (unverified) |
| Atlassian | atlassian.com careers - listings JSON endpoint (unverified) |
| American Express | not on `aexp.eightfold.ai` (404); platform unknown |
| Deutsche Bank | careers.db.com - single-page app over a JSON API (beesite) |
| Standard Chartered | the SuccessFactors CSB site lists 1 job worldwide; the real listing is elsewhere |
| BT Group | same: its CSB site lists 4 jobs worldwide |
| Blackhawk | iCIMS classic (`careers-blackhawknetwork.icims.com`) - no adapter for classic iCIMS |
| Dassault Systemes, Fastenal (Radancy), SoundHound, Societe Generale, Grant Thornton, HDFC, L&T | site found, platform not identified |
| Apexon, NAVEX, Smiths Detection, Syngene, 7-Eleven | Workday tenants whose cxs path differs from the careers URL (422); need the real `/wday/cxs/<tenant>/<site>` from the browser's network tab |
| Lytx | Workday board is real but has 2 jobs, both US |

## Moved or wrong (found by the 2026-10-06 audit)

- **Atomicwork**: Greenhouse board taken down 2026-10-03; now read from its careers page (`custom`).
- **Cyware**: left Freshteam for Greenhouse (`cyware`).
- **Proximity Works**: left Workable for its own site (`custom`).
- **3Pillar**: Lever board empty; now on Darwinbox (renders only through Firecrawl, mostly LatAm/US roles). Row commented out.
- **HappyLocate, VIDA, Codvo, People10**: Freshteam accounts closed. HappyLocate lists no jobs, VIDA's careers page is a 404, Codvo's 3 roles are JS-rendered and mostly US, People10 moved to Zoho Recruit. Rows commented out.
- **Porter, Bounce**: the rows pointed at different companies with the same name (a US Porter on Lever, the Lisbon luggage Bounce on Ashby). Commented out.

## No public job board found (unchanged from 2026-09-16)

LinkedIn job alerts or a referral contact are the only route for these. Mostly small or
non-tech firms:

ADA, Andpayment, Aster, Aukera, Blackbox, Bluehill, Bravura Tech, Brick&Bolt, CadreSports,
Catechy Solutions, Cessna, Chaarana Labs, CliniExperts, Creysto, Diligent, Dohful, Earnifi,
Egis, Espirito, Euphoric Thought, Fashion UK, Federal Bank, GoComet, Healjour, IML, Intueri,
Iylon, KNCC, Kepler Aerospace, Les Baskets, Limitless, Lingopanda, Madhan Mohan, Mazle.ai,
Mewesalus Care, Mining Grid, Monk Studios, NI, NPF, NSRCEL IIMB, NST, Netchex, Nirad, OSBI,
Opptra, Pazy, Pragma, Primetrace, Quantacus AI, Quantum QMCO, RBIH, RP & Co, Resyynth
Agritech, Rocket, Rootpay, Roots India, SKS Advisor, STG Labs, Sechpoint, Skilign, Svarapps,
SwiffyLabs, TCL, TekChant, TinkerKraft, Triloma, Triplespeed, UBL, Userfacet.

Also from the 2026-09-16 lists, still uncovered and unchecked this round: Analyttica, Ati
Motors, Storylane, Netcore Unbxd, Think Design (their Freshteam accounts do not exist),
GIVA, Snapmint, StatusNeo, MrMed, R360, Indus DC Ventures, Zemoso (Keka slugs were
guesses), Tricon Infotech (Zoho Recruit), FinBox (Reczee), Ather, PhonePe, ClearTax, Uni
Cards, Gale, Transak, Xflow (private Ashby boards), Impact Analytics, Indegene, Anheuser-
Busch, Social Panga, TEKsystems, HashedIn, Loginsoft, Manipal
Hospitals, Nextwealth, Polestar Analytics, STL Digital, UnitedLayer, Flipkart (TurboHire,
renders empty).
