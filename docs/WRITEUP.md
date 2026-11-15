**To:** assessment@enam.com
**Subject:** IIT Delhi – Founding AI Engineer - 2027

Hello ENAM team,

Please find my submission for the FINN build challenge.

- **Prototype (open on a phone):** `<render-url>` (live backend) · https://darkrealm018.github.io/finn/ (static fallback, no backend)
- **Repository:** https://github.com/DarkRealm018/finn

## What I built

FINN is one ranked stack and one input box.

**Brief (Push).** Every NSE filing across an 18-stock mock portfolio for a window you choose (24h by default). Each filing is classified and de-duplicated, then ranked by *materiality × your category interests × position size*, with a boost when the stock moved on it or when someone in your network is linked. Over the last 30 days, 227 raw filings became 35 cards that need you, about 40 one-liners, and about 90 routine items collapsed into a single line. Each card type has its own shape:

- **Results:** Revenue, EBITDA, PBT and PAT, QoQ and YoY, read directly from the results filing's XBRL (e.g. Dixon Q2 FY27: revenue ₹21,794 cr; ICICI Bank: NII +16% YoY). Banks show NII/PPOP and NBFCs PPOP, because EBITDA means nothing for a lender.
- **Management change:** who is coming or going, a bio taken from the filing's SEBI "brief profile" annexure, their career organisations, and your route to them. Example: HDFC Bank's new Chief Compliance Officer, V. N. Srivatsan, came from ICICI Bank. FINN shows *You → Rohan Iyer (IIM-A classmate) → Srivatsan: "was in his compliance team at ICICI, 2016–20."* For Trent's board change, it routes to Rajiv Dube through two ex-Tata Motors contacts and to Kalpana Merchant through an ex-AZB lawyer. For Asian Paints' incoming chairman, Leo Puri, the November filing had no profile, so FINN found it in the company's September appointment filing. It extracted his career (Apax, J.P. Morgan, CEO of UTI AMC, Warburg Pincus, McKinsey; Oxford and Cambridge) and noticed *"You were both at McKinsey & Company."* Exits are surfaced too: HDFC Bank's Deputy MD stepping down tops the 30-day Brief, with the ex-HDFC contact best placed to explain what it means.
- **Material events:** the contact best placed to explain the event, and why (inside, alumni, competitor, customer, supplier, same group, or sector expert).

**Watch (triggered alerts).** You set what "unusual" means with two sliders (price ±X%, volume Y× the 20-day average) and pick which filing types to subscribe to. Each move is tagged either *explained by* a filing or "no filing explains it — ask X."

**Research (Pull).** Works for any NSE ticker. It covers business and revenue mix, financial snapshot, trajectory (3/5/10-year CAGRs, margin direction), balance sheet and working-capital cycle, cash conversion, **promised vs delivered**, bull/bear, and who to call.

Promised vs delivered is the part I'm proudest of. FINN reads the last four concall transcripts and splits them into speaker turns. It drops analyst questions, using analyst names learned from "the next question is from the line of…". From management's own words it keeps forward-looking, quantified statements, each with its page number. It then checks each one against the quarters reported *after* that call: year-on-year or quarter-on-quarter as stated, full-year or near-term. Verdicts are delivered, missed, on track, behind, too early, or not checkable.

Some real outputs:
- Kaynes guided "₹4,500 cr ≈ 55% growth"; revenue is +40.6% one quarter in, so it is marked *behind*.
- CreditAccess's AUM and NIM guidance is flagged "not checkable from the P&L; see the investor deck" rather than tested against the wrong number.
- FINN also notices when management gave *no* numeric guidance, and what analysts kept pressing on (Kaynes: working capital).

**Network.** It is woven into every Brief card, the top of every research page, alerts and Ask. Its own tab is not a contact list but what the network notices:
- a coverage map of your book and where you're thin;
- your deepest benches;
- people one hop away who appeared in your portfolio's filings;
- "keep warm" nudges with a drafted opener when a former employer of a quiet contact files something material.

**Ask FINN.** One box, typed or spoken (Web Speech API). Examples: "who knows Rajiv Dube", "prep me for Dixon" (questions built from missed guidance and analyst pushback, plus who to call first), "experts on rural lending", "what changed this week".

## Key decisions and tradeoffs

1. **Read numbers; never generate them.** Results come from XBRL, history from screener's standardised statements (which are lifted from filings), and quotes from transcripts with page links. A model is only ever handed numbers I computed, so it can phrase and judge but not invent.
2. **Rules first, a model on the long tail.** Filing classification is the high-volume step, and rules handle it free, instantly and auditably. NSE's own subject line is unreliable (Dixon's Q2 results were filed as "Change in Management"), so FINN classifies on content: NSE's text, then the PDF's "Sub:" line and first pages, but only for filings that might be material. The cheap model only sees what lands in "General Updates".
3. **Treat filings as the people database.** SEBI requires a brief-profile annexure for every appointment, which makes it the most reliable public bio of an incoming director or KMP. When a filing lacks one (e.g. a shareholder-approval notice), FINN looks back through the company's earlier filings for it.
4. **Network answers are always (who, why, how strong).** Tie strength blends relationship type, recency and frequency. A cold contact who is perfectly placed still surfaces, flagged as cold. Route strength is tie × link. An explicit note ("reported to him for 6 years") beats "same company, may have overlapped", which beats "same school".
5. **Honesty over coverage.** If a promise can't be checked from the data, FINN says so and why (segment-level guidance, constant currency, NIM vs OPM) instead of forcing a verdict.
6. **Regression-tested on real failures.** `tests/` holds 18 offline tests. Each fixture is an excerpt from a real filing or transcript that once broke a rule: results filed under the wrong subject; a SEBI template that mentions every action; "Mr . Rajiv Dube" with a stray space; past-tense statements posing as guidance; segment-level promises. Writing them surfaced two silent bugs that touched every document: a sentence splitter that broke on every "Mr.", and a regex alternation that let "appoint" anywhere count as an action.
7. **Thin but working.** No framework and no build step: three static files and a small FastAPI app. The client falls back to a frozen snapshot automatically, so the demo runs even without a backend.

## Architecture

**Data pipelines.**

| Source | What and how | Refresh |
|---|---|---|
| Yahoo chart API | EOD price and volume | cached 6h |
| NSE API (browser-like session) | Announcements, and results XBRL via both the integrated-filing and legacy endpoints | announcements 20 min; XBRL forever (immutable) |
| screener.in | Standardised statements and the document index (ARs, transcripts) | 12h |
| Company PDFs | Text and speaker turns | cached forever |

Everything goes through a content-addressed disk cache with stale-on-error. If NSE blocks a cloud IP, the last good copy is served.

**Linking.** Organisations resolve through a gazetteer of aliases plus an industry map (peers, customers, suppliers, group). People are linked by fuzzy name match to the investor's notes, and by organisation and school overlap with contacts' career histories. In production the gazetteer would be generated from filings (related-party notes, segment notes) and a licensed company graph.

**Security and privacy.**
- The network is the investor's personal data, so it lives only in their instance. The API is behind a per-investor token (`FINN_TOKEN`; in production, SSO plus per-tenant encryption at rest with a KMS-held key).
- Contact data is never sent to a model in identifiable form. The Network layer is pure code; models only see public filings and transcripts.
- Retention: network data is kept until the investor deletes it. Derived data (paths, warmth scores) is recomputed, not stored. Logs carry no names.
- Screen-share mode blurs every name with one tap.
- For real profiles I would not scrape LinkedIn, which its terms forbid. Instead: (a) the investor's own LinkedIn "connections" data export plus calendar and CRM, imported with explicit consent; (b) a licensed people-data API for public professional profiles; (c) exchange filings for anyone in the news. Third parties in the network get a documented legitimate-interest basis under India's DPDP Act, a minimal field set (employer, role, tenure, school), and a deletion path.
- **In this prototype every contact is synthetic.** Real people appear only through their own public filings.

**LLMs and agents.**

| Step | Model | Why |
|---|---|---|
| Filing classification | Rules → **Claude Haiku 5.5** only for "General Updates" | Volume; cheap model only on the long tail |
| Profile → career graph | Haiku (rules + gazetteer fallback) | Structured extraction |
| Guidance extraction from transcripts | Haiku | Long, cheap reads, once per document |
| Promise verdicts, bull/bear | **Claude Opus 5.5** at low/medium effort | The two steps where judgement matters |
| Ask routing | Rules → Haiku with a fixed tool enum | Can only choose a tool, never answer from memory |

All calls are JSON-schema-constrained, cached by content hash, and use a cached system-prompt prefix.

There is no autonomous agent loop, and I chose that deliberately. Every flow is a fixed pipeline, because predictability and cost matter more here than open-ended planning. The one "agentic" step is the Ask router choosing among tools.

**Every feature has a deterministic fallback, and the submitted demo runs with no model at all (cost: ₹0).** With a key, my estimate is about $0.01 to extract a company's four transcripts plus about $0.04 for its judgement calls, once, then cached. That is an estimate from token counts, not measured; I had no API key in this environment, so the model path is implemented but untested end to end.

**Cloud.**
- **Today:** one small Python web service (Render free tier) serving API and client, plus a GitHub Pages static fallback.
- **Production:** a scheduled ingest worker (EOD prices after 4pm IST, filings every few minutes during market hours) writing to Postgres and object storage; the API on a small container; web push notifications for triggered alerts; per-tenant encrypted storage for the network; and Anthropic's API, or the same models via Bedrock or Vertex for data-residency needs.

## Deliberately left out

- Real push notifications (alerts compute and badge, but nothing is sent to the OS yet).
- Annual-report deep reads: contingent liabilities, RPTs, auditor qualifications. The links are there; the extractor isn't.
- BSE (its API blocks scripted access, and NSE covers the universe).
- Intraday data.
- Authentication beyond a shared token.
- An editable network UI (the network is a JSON file the investor would import).

## Where I'd take it next

1. **Network ingestion from consent-based exports** (LinkedIn data export, calendar, email headers). FINN would learn tie strength from real interaction frequency and write a note after every meeting, so "knows" edges accumulate.
2. **Annual-report reader** for the red flags pull should catch: contingent liabilities, RPT growth, auditor remarks, pledging trends.
3. **Promise tracking as a live ledger.** Each new transcript adds to a per-company credibility score that feeds Push ranking: a miss by a management team that usually delivers is bigger news.
4. **A morning voice brief.** The lede plus top three cards, read aloud on the commute, with "call Rohan" as a spoken follow-up.

Thank you for the thoughtful brief. I enjoyed building this.

Best regards,
`<your name>`
IIT Delhi
