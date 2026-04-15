# opintel-feed

A Python tool for generating weekly Operational Intelligence (OpIntel) cybersecurity briefing documents. Run it the night before a briefing to automatically fetch, screen, and summarize relevant cybersecurity news from RSS feeds into a single HTML document ready to copy-paste into Box Notes.

**[See example output](output/example_briefing.md)**

---

## Table of Contents

- [How It Works](#how-it-works)
- [Article Selection Methodology](#article-selection-methodology)
- [Initial Configuration](#initial-configuration)
- [Setup](#setup)
- [Usage](#usage)
- [Configuration Reference](#configuration-reference)
- [Output Format](#output-format)

---

## How It Works

The tool runs a multi-stage pipeline each time it is invoked:

```
Fetch (RSS + manual)
  → Prior-week overlap filter
  → Heuristic pre-filter (dedup, sponsored, recaps, off-topic, keywords)
  → Phase 1: Claude Haiku — AI relevance validation
  → Phase 2: Claude Sonnet — Summarization + categorization
  → HTML render
```

### Stage 1 — Fetch

All configured RSS feeds are fetched using a browser-like User-Agent to avoid bot-blocking. Articles published outside the lookback window (default: last 7 days) are discarded. Per-feed article caps are applied at this stage. Manual includes from `manual_includes.json` are fetched directly by URL and added to the pool.

### Stage 2 — Prior-week overlap filter

The tool fetches a double-width window (e.g., 14 days if `--days 7`) and splits it into a *current* window and a *prior* window. Any article in the current window whose title is substantially similar ([Jaccard similarity](https://en.wikipedia.org/wiki/Jaccard_index) ≥ 0.5 on normalized word sets) to an article from the prior window is dropped — it was likely already covered in the previous briefing.

### Stage 3 — Heuristic pre-filter

A series of zero-cost checks run before any API calls are made. In order:

1. **Same-window deduplication** — Articles from different feeds covering the same story are merged into a single entry (Jaccard similarity ≥ 0.4 on normalized title word sets, or exact CVE ID overlap). Both URLs are preserved.
2. **Patch Tuesday deduplication** — All Patch Tuesday articles (detected by title or body text) are collapsed into a single entry. The first article becomes the primary; subsequent ones have their URLs merged into it so all sources are represented in the output.
3. Per-article checks (non-manual articles only):
   - **Sponsored content** — Dropped if the URL or text signals sponsorship (`/sponsored/`, "brought to you by", "partner content", etc.).
   - **Recaps and digests** — Dropped if the title signals it is a roundup rather than a news item ("Week in Review", "Monthly Digest", "Top 10 Stories", etc.).
   - **Non-news content** — Dropped if the article is a podcast, video, webinar, speaking engagement announcement, Windows KB release note, CISO interview column, startup funding announcement, or vendor product launch (e.g. "Vendor Launches XYZ Platform").
   - **CVSS ≥ 9.0** — Immediately confirmed regardless of other rules. Both CVSSv2 and CVSSv3 scores are detected.
   - **Keyword match** — Confirmed if the article mentions a term from `include.json` and does not also match a term from `exclude.json`.
   - **Off-topic guard** — Articles that pass none of the above and contain no recognizable infosec vocabulary are discarded before being sent to the AI. This is the primary cost-control gate.

Articles that survive this stage are either *confirmed* (guaranteed include) or *candidates* (need AI validation).

### Stage 4 — Phase 1: Haiku validation

Candidate articles are sent to `claude-haiku-4-5-20251001` in batches of 10. The system prompt (which defines the relevance criteria) is prompt-cached to reduce cost on subsequent batches. Claude assesses each article against:

- Specific impact on higher education institutions
- Geopolitical cybersecurity events affecting US organizations
- Data breaches, BEC/phishing, ransomware affecting university-like organizations
- Well-known threat actors targeting higher ed or critical infrastructure
- Cybersecurity tools and vulnerabilities of broad enterprise IT relevance

Haiku is explicitly instructed to reject vendor product launch announcements and press releases — even when the product category is security-related.

Results are cached to disk (7-day TTL by default) so re-runs within the same week do not re-process articles already validated.

### Stage 5 — Phase 2: Sonnet summarization

All confirmed + validated articles are sent to `claude-sonnet-4-6` in batches of 20 for summarization. Claude is instructed to:

- Write 2–4 concise bullet points per article
- Assign each article to a category
- Merge articles that clearly cover the same underlying news event into a single entry (synthesized title, all URLs listed)

A post-summarization dedup pass removes any standalone entry whose URL already appears in a merged entry, preventing the same article from appearing twice when Claude both merges it and summarizes it independently.

Summaries are also cached, so re-runs do not re-summarize already-processed articles.

### Stage 6 — Render

The final article set is grouped by category and written to an HTML file. URLs are rendered as plaintext (not hyperlinks) for clean copy-paste into Box Notes. Articles within each category are separated by a line break.

---

## Article Selection Methodology

### Guaranteed inclusions

An article is automatically included if **any** of these conditions are true:

| Condition | Rule |
|---|---|
| Critical vulnerability | CVSS v2 or v3 score ≥ 9.0 detected in article text |
| Deployed software | Title or text mentions a term from `include.json`, and the article does **not** also mention a term from `exclude.json` |
| Manual include | URL listed in `manual_includes.json` |

The CVSS ≥ 9.0 rule **overrides** the exclude list — a critical vulnerability in an excluded product is still included.

### AI-validated inclusions

Articles that don't meet the above criteria but pass the off-topic guard are evaluated by Claude Haiku against the criteria in [Stage 4](#stage-4--phase-1-haiku-validation) above.

### Output categories

Articles are assigned to one of four categories by Claude Sonnet:

| Category | Intended content |
|---|---|
| **Patching/Security Concerns** | Vulnerabilities, CVEs, patches, exploits, misconfigurations |
| **Malware/Ransomware/BEC/Scams** | Malware campaigns, ransomware incidents, phishing, BEC, fraud |
| **Nation States and GeoPolitics** | State-sponsored attacks, geopolitical cyber events, espionage |
| **Other News** | Incidents, breaches, policy, research, and anything else |

---

## Initial Configuration

### RSS Feeds (`config/feeds.json`)

| Feed | Notes |
|---|---|
| The Hacker News | |
| Krebs on Security | |
| BleepingComputer | |
| Dark Reading | Capped at 5 articles/run |
| SecurityWeek | |
| Schneier on Security | |
| The Record (Recorded Future) | |
| CyberScoop | |
| 404 Media | |
| Help Net Security | |
| Sophos News | |
| Malwarebytes Blog | Disabled (max: 0) |
| Proofpoint Blog | |
| NIST Cybersecurity | |
| NCSC (UK) | |
| Graham Cluley | |
| ISC SANS | Disabled (max: 0) |
| Have I Been Pwned Breaches | |

Set `"max": 0` to disable a feed without removing it. Set `"max": N` to cap articles per run.

### Include keywords (`config/include.json`)

These terms represent software and services in the local environment. An article mentioning any of these is automatically included (unless it also matches an exclude keyword):

`Active Directory`, `Aruba Wireless`, `AWS`, `Axonius`, `Azure`, `Canvas`, `Cisco ASA`, `Cisco Duo`, `Coreview`, `Delinea`, `Fischer Identity`, `FortiGate`, `FortiMail`, `FortiOS`, `Grouper`, `InCommon`, `InTune`, `Jamf`, `Juniper Router`, `Keycloak`, `Microsoft Entra`, `Microsoft Windows`, `Oracle`, `Qualys`, `Salesforce`, `SecureW2`, `ServiceNow`, `Shibboleth`, `Splunk`, `Sympa`, `VMware`, `Workday`, `Zoom`

These are treated as whole-word, case-insensitive keyword matches — you can add general terms like `"ransomware"` or `"zero-day"` and they will work identically to product names.

### Exclude keywords (`config/exclude.json`)

Articles matching these terms are suppressed from the keyword-match confirmation path (but can still be included via CVSS ≥ 9.0 or AI validation):

`CyberArk`, `GCP`, `Palo Alto`

These represent products not in the local environment that would otherwise generate false positives from the include list.

---

## Setup

### Prerequisites

- Python 3.10 or later
- An [Anthropic API key](https://console.anthropic.com) with billing enabled

### Installation

```bash
git clone <repo-url>
cd opintel-feed
pip install -r requirements.txt
```

### API key

Copy `.env.example` to `.env` and add your key:

```bash
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

The `.env` file is gitignored and will never be committed.

### Verify the setup

Run a dry run to confirm feeds are reachable and the pipeline works without making any API calls:

```bash
python3 main.py --dry-run
```

This prints the full list of articles that would be processed and a worst-case cost estimate.

---

## Usage

### Standard run (night before briefing)

```bash
python3 main.py
```

Output is written to `output/briefing_YYYY-MM-DD.html`. If that file already exists (e.g., from an earlier run that day), a `_2` suffix is appended automatically.

### Common options

```
--dry-run            Print articles and worst-case cost estimate; make no API calls
--days N             Lookback window in days (default: 7)
--max-articles N     Cap on candidates sent to AI validation (default: 50)
--no-cache           Ignore cached validation and summary results; reprocess all articles
                     (new results are still written to cache)
--config-dir PATH    Use a different config directory
--output PATH        Write output to a specific file path
--cache-dir PATH     Use a different cache directory
```

### Manually including a specific article

Add it to `config/manual_includes.json`:

```json
[
  {"url": "https://example.com/article"},
  {"url": "https://example.com/other", "category": "Nation States and GeoPolitics"}
]
```

The `category` field is optional. If omitted, Claude assigns the best-fit category.

### Adding or adjusting feeds

Edit `config/feeds.json`. Each entry is either a plain URL string or an object:

```json
[
  "https://example.com/feed.xml",
  {"url": "https://chatty-feed.com/rss", "max": 5},
  {"url": "https://disabled-feed.com/rss", "max": 0}
]
```

---

## Configuration Reference

| File | Format | Purpose |
|---|---|---|
| `config/feeds.json` | Array of strings or `{"url", "max"}` objects | RSS feeds to poll |
| `config/include.json` | Array of strings | Keywords/products that trigger automatic inclusion |
| `config/exclude.json` | Array of strings | Keywords that suppress inclusion (except CVSS ≥ 9.0) |
| `config/manual_includes.json` | Array of `{"url", "category?"}` objects | Force-included articles |
| `.env` | Key=value | `ANTHROPIC_API_KEY` and optional overrides |

### Environment variable overrides

| Variable | Default | CLI equivalent |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(required)* | — |
| `OPINTEL_MAX_ARTICLES` | `50` | `--max-articles` |
| `OPINTEL_CACHE_TTL_DAYS` | `7` | — |

---

## Output Format

Each article in the HTML output follows this structure:

```
• Article Title in Bold
    • https://plaintext-url.com/article
    • First summary bullet point.
    • Second summary bullet point.
    • Optional third or fourth bullet point.
```

If multiple articles were merged as the same story, all URLs are listed as separate second-level bullets before the summary.

Articles within each category are separated by a line break. The output is intentionally minimal HTML — suitable for copy-pasting into Box Notes or similar rich-text editors. Open the file in a browser to review and curate before copying.

### Token usage report

After a full run, a cost summary is printed to the console:

```
========================================================================
Model                               Input   Output  CacheRd  CacheWr
------------------------------------------------------------------------
claude-haiku-4-5-20251001           8,400      280    4,200      250
claude-sonnet-4-6                   9,100    1,050    3,600      400
------------------------------------------------------------------------
Estimated cost                                                  $0.0231
========================================================================
```
