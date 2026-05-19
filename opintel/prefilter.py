from __future__ import annotations

import re

from .config import AppConfig, PreviousInclude
from .models import Article

_STOPWORDS = frozenset([
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "its", "it", "this", "that", "these",
    "those", "as", "up", "out", "into", "over", "after", "new", "how",
    "what", "why", "when", "where", "who", "which", "not", "also", "about",
])

_CVE_RE = re.compile(r"CVE-\d{4}-\d+", re.IGNORECASE)

_TITLE_SIMILARITY_THRESHOLD = 0.4

# ---------------------------------------------------------------------------
# Recap / roundup / digest detection
# ---------------------------------------------------------------------------
_RECAP_TITLE_RE = re.compile(
    r"""
    \bweek(?:ly)?\s+(?:in\s+(?:review|security)|roundup|recap|digest)\b
  | \bmonth(?:ly)?\s+(?:in\s+review|roundup|recap|digest)\b
  | \bthis\s+week\s+in\b
  | \b(?:weekly|monthly|daily)\s+(?:digest|newsletter|roundup|recap|summary)\b
  | \b(?:news|security)\s+(?:digest|roundup|recap|summary)\s*(?:[#]\d+|\d+)?\b
  | \btop\s+\d+\s+(?:cybersecurity|security|infosec)\s+(?:stories|news|articles)\b
  """,
    re.IGNORECASE | re.VERBOSE,
)

# ---------------------------------------------------------------------------
# Podcast / video / non-news content detection
# ---------------------------------------------------------------------------
_NON_NEWS_URL_RE = re.compile(
    r"/(?:podcast|video|webinar|episode|listen|watch|audio)/",
    re.IGNORECASE,
)

_NON_NEWS_TITLE_RE = re.compile(
    r"""
    \[(?:podcast|video|webinar|audio|interview)\]
  | \bpodcast(?:\s+episode)?\b
  | \bepisode\s+\d+\b
  | \bep\.\s*\d+\b
  | \b(?:listen|watch)\s*(?:now|here)?\s*[:\|]
  | \bwebinar\b
  | \bvideo(?:\s+interview)?\b
  | \bupcoming\s+speaking\s+engagements?\b
  | \bspeaking\s+(?:engagement|schedule|appearance)s?\b
  | \bWindows\s+\d+\s+(?:cumulative\s+update|KB\d{5,})\b
  | \bKB\d{5,}\b
  | \bCISO\s+Conversations?\b
  | \b(?:emerges?|coming\s+out)\s+from\s+stealth\b
  | \braises?\s+\$[\d.]+\s*(?:M|B|million|billion)\b
  | \$[\d.]+\s*(?:M|B)\s+(?:seed|series\s+[A-Z]|funding|round)\b
  | \bin\s+(?:seed|series\s+[A-Z])\s+funding\b
    # Vendor product launch / press release
  | \b(?:launches?|introduces?|unveils?)\s+(?:\w[\w-]*\s+){1,10}(?:platform|solution|suite|engine)\b
    # Versioned product release announcements (e.g. "v2.3 released", "version 26.04 now available")
  | \bv?\d+\.\d[\d.]*\s+(?:released?|now\s+available|generally\s+available|is\s+(?:here|out))\b
    # Long-term support / multi-year maintenance announcements (product marketing, not security news)
  | \b\d+[-\s]year\s+(?:security\s+)?(?:support|maintenance)\b
  """,
    re.IGNORECASE | re.VERBOSE,
)

# ---------------------------------------------------------------------------
# Infosec relevance guard (applied to candidates before sending to AI)
# ---------------------------------------------------------------------------
_INFOSEC_KEYWORDS = re.compile(
    r"""
    \b(?:
      vulnerabilit(?:y|ies)|exploit(?:ed|ing|ation)?|malware|ransomware
    | phish(?:ing)?|breach(?:ed)?|hack(?:ed|er|ing)?
    | cyber(?:security|attack|crime|espionage|threat|incident)?
    | security|CVE|patch(?:ed|ing)?|zero.?day|threat(?:s)?
    | attack(?:ed|ing|ers?)?|incident|APT|botnet|trojan|spyware
    | credential(?:s)?|authentication|authorization|identity|privilege
    | encrypt(?:ion|ed)?|firewall|intrusion|detection|prevention
    | exfiltrat(?:ion|ed)?|data\s+(?:breach|theft|leak|loss)
    | DDoS|DoS|SIEM|SOC|infosec|pentest|red\s+team|blue\s+team
    | CISA|NCSC|NVD|NIST|FBI\s+cyber|NSA\s+cyber
    | privacy|compliance|forensic|backdoor|rootkit|keylogger
    | scam|fraud|BEC|phish|spear.?phish|vish(?:ing)?|smish(?:ing)?
    | supply.?chain|third.?party\s+risk|vendor\s+risk
    | nation.?state|threat\s+actor|APT\s+group
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# ---------------------------------------------------------------------------
# Sponsored content detection
# ---------------------------------------------------------------------------
_SPONSORED_URL_RE = re.compile(
    r"/(?:sponsor(?:ed)?|partner(?:-content)?|advertorial|paid(?:-content)?|promoted)/",
    re.IGNORECASE,
)

_SPONSORED_TEXT_RE = re.compile(
    r"""
    \[sponsored\]
  | \bsponsored\s+(?:post|content|article|by)\b
  | \bbrought\s+to\s+you\s+by\b
  | \bpaid\s+(?:post|content|article|advertisement)\b
  | \badvertorial\b
  | \bpartner\s+content\b
  | \bthis\s+(?:post|article|content)\s+(?:is|was)\s+sponsored\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# ---------------------------------------------------------------------------
# CVSS detection
# ---------------------------------------------------------------------------
_CVSS_NUMERIC_RE = re.compile(
    r"CVSS(?:v[23])?(?:\s+(?:Base\s+)?Score)?[:\s]+([0-9]{1,2}\.[0-9])",
    re.IGNORECASE,
)

_CVSS_VECTOR_SCORE_RE = re.compile(r"/([0-9]{1,2}\.[0-9])\s*$")

_CVSS_VECTOR_RE = re.compile(r"CVSS:[0-9.]+/AV:[NALP]/", re.IGNORECASE)

_PATCH_TUESDAY_RE = re.compile(r"\bpatch\s+tuesday\b", re.IGNORECASE)

_CROSS_WINDOW_THRESHOLD = 0.5


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _title_words(title: str) -> frozenset[str]:
    words = re.sub(r"[^a-z0-9\s]", "", title.lower()).split()
    return frozenset(w for w in words if w not in _STOPWORDS and len(w) > 2)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _cve_ids(text: str) -> frozenset[str]:
    return frozenset(m.upper() for m in _CVE_RE.findall(text))


def _term_matches(text: str, term: str) -> bool:
    pattern = re.compile(
        r"(?<![A-Za-z0-9_-])" + re.escape(term) + r"(?![A-Za-z0-9_-])",
        re.IGNORECASE,
    )
    return bool(pattern.search(text))


def extract_cvss_scores(text: str) -> list[float]:
    scores: list[float] = []
    for match in _CVSS_NUMERIC_RE.finditer(text):
        try:
            scores.append(float(match.group(1)))
        except ValueError:
            continue
    for chunk in text.split():
        if _CVSS_VECTOR_RE.search(chunk):
            m = _CVSS_VECTOR_SCORE_RE.search(chunk)
            if m:
                try:
                    scores.append(float(m.group(1)))
                except ValueError:
                    continue
    return scores


def _max_cvss(text: str) -> float | None:
    scores = extract_cvss_scores(text)
    return max(scores) if scores else None


# ---------------------------------------------------------------------------
# Multi-article deduplication passes
# ---------------------------------------------------------------------------

def deduplicate_articles(articles: list[Article]) -> list[Article]:
    seen: list[tuple[Article, frozenset[str], frozenset[str]]] = []
    result: list[Article] = []

    for article in articles:
        words = _title_words(article.title)
        cves = _cve_ids(article.raw_text)

        merged = False
        for primary, primary_words, primary_cves in seen:
            cve_overlap = cves and primary_cves and bool(cves & primary_cves)
            title_overlap = _jaccard(words, primary_words) >= _TITLE_SIMILARITY_THRESHOLD
            if cve_overlap or title_overlap:
                if article.url not in primary.duplicate_urls and article.url != primary.url:
                    primary.duplicate_urls.append(article.url)
                merged = True
                break

        if not merged:
            seen.append((article, words, cves))
            result.append(article)

    deduped = len(articles) - len(result)
    if deduped:
        print(f"  Heuristic dedup: merged {deduped} article(s) into existing entries.")
    return result


def deduplicate_patch_tuesday(articles: list[Article]) -> list[Article]:
    result: list[Article] = []
    kept: Article | None = None
    dropped = 0
    for article in articles:
        if _PATCH_TUESDAY_RE.search(article.title) or _PATCH_TUESDAY_RE.search(article.raw_text):
            if kept is not None:
                if article.url not in kept.duplicate_urls and article.url != kept.url:
                    kept.duplicate_urls.append(article.url)
                dropped += 1
                continue
            kept = article
        result.append(article)
    if dropped:
        print(f"  Patch Tuesday: kept 1, merged {dropped} duplicate(s) into primary entry.")
    return result


def filter_prior_window_overlaps(
    current: list[Article],
    prior: list[Article],
) -> list[Article]:
    if not prior:
        return current

    prior_index = [_title_words(a.title) for a in prior]
    result: list[Article] = []
    excluded = 0

    for article in current:
        words = _title_words(article.title)
        if any(_jaccard(words, pw) >= _CROSS_WINDOW_THRESHOLD for pw in prior_index):
            excluded += 1
        else:
            result.append(article)

    if excluded:
        print(f"  Prior-week overlap: excluded {excluded} article(s) likely covered in the previous briefing.")
    return result


def filter_previous_includes(
    articles: list[Article],
    previous_includes: list[PreviousInclude],
) -> list[Article]:
    if not previous_includes:
        return articles

    prev_urls = frozenset(pi.url for pi in previous_includes)
    prev_title_words = [_title_words(pi.title) for pi in previous_includes if pi.title]

    result: list[Article] = []
    excluded = 0

    for article in articles:
        if article.url in prev_urls or any(u in prev_urls for u in article.duplicate_urls):
            excluded += 1
            continue
        words = _title_words(article.title)
        if any(_jaccard(words, pw) >= _CROSS_WINDOW_THRESHOLD for pw in prev_title_words):
            excluded += 1
            continue
        result.append(article)

    if excluded:
        print(f"  Previous briefings: excluded {excluded} article(s) already covered.")
    return result


_FINAL_POOL_THRESHOLD = 0.3
_FINAL_POOL_BODY_THRESHOLD = 0.12
_FINAL_POOL_BODY_WORDS = 100


def _body_words(text: str, n: int = _FINAL_POOL_BODY_WORDS) -> frozenset[str]:
    words = re.sub(r"[^a-z0-9\s]", "", text.lower()).split()[:n]
    return frozenset(w for w in words if w not in _STOPWORDS and len(w) > 2)


def _distinctive_title_words(all_words: list[frozenset[str]]) -> frozenset[str]:
    """Words appearing in ≤2 titles in the pool — likely distinctive named entities."""
    freq: dict[str, int] = {}
    for words in all_words:
        for w in words:
            freq[w] = freq.get(w, 0) + 1
    return frozenset(w for w, count in freq.items() if count <= 2)


def deduplicate_final_pool(articles: list[Article]) -> list[Article]:
    """Tighter dedup pass on the post-Haiku pool before summarization.

    Four merge signals, any one of which triggers a merge:
    1. CVE ID overlap in body text
    2. Title Jaccard >= 0.3 (tighter than pre-filter's 0.4)
    3. Shared distinctive named entity — a word appearing in <=2 titles in the
       pool (catches "DirtyDecrypt", "Grafana", etc. where titles vary widely)
    4. Body text Jaccard on first 100 words >= 0.12
    """
    if not articles:
        return articles

    all_title_words = [_title_words(a.title) for a in articles]
    distinctive = _distinctive_title_words(all_title_words)

    seen: list[tuple[Article, frozenset[str], frozenset[str], frozenset[str]]] = []
    result: list[Article] = []

    for i, article in enumerate(articles):
        title_w = all_title_words[i]
        body_w = _body_words(article.raw_text)
        cves = _cve_ids(article.raw_text)

        merged = False
        for primary, primary_title_w, primary_body_w, primary_cves in seen:
            if (
                (cves and primary_cves and bool(cves & primary_cves))
                or _jaccard(title_w, primary_title_w) >= _FINAL_POOL_THRESHOLD
                or bool(title_w & primary_title_w & distinctive)
                or _jaccard(body_w, primary_body_w) >= _FINAL_POOL_BODY_THRESHOLD
            ):
                if article.url not in primary.duplicate_urls and article.url != primary.url:
                    primary.duplicate_urls.append(article.url)
                merged = True
                break

        if not merged:
            seen.append((article, title_w, body_w, cves))
            result.append(article)

    deduped = len(articles) - len(result)
    if deduped:
        print(f"  Final pool dedup: merged {deduped} duplicate(s) into existing entries.")
    return result


# ---------------------------------------------------------------------------
# Single-article content type checks
# ---------------------------------------------------------------------------

def _is_recap(article: Article) -> bool:
    return bool(_RECAP_TITLE_RE.search(article.title))


def _is_non_news(article: Article) -> bool:
    if _NON_NEWS_URL_RE.search(article.url):
        return True
    if _NON_NEWS_TITLE_RE.search(article.title):
        return True
    return False


def is_sponsored(article: Article) -> bool:
    if _SPONSORED_URL_RE.search(article.url):
        return True
    if _SPONSORED_TEXT_RE.search(article.raw_text):
        return True
    return False


def _has_infosec_content(article: Article) -> bool:
    return bool(_INFOSEC_KEYWORDS.search(article.raw_text))


# ---------------------------------------------------------------------------
# Main pre-filter
# ---------------------------------------------------------------------------

def prefilter_articles(
    articles: list[Article],
    config: AppConfig,
) -> tuple[list[Article], list[Article]]:
    articles = deduplicate_articles(articles)
    articles = deduplicate_patch_tuesday(articles)

    confirmed: list[Article] = []
    candidates: list[Article] = []
    skipped: dict[str, int] = {"sponsored": 0, "recap": 0, "non_news": 0, "off_topic": 0}

    for article in articles:
        if article.is_manual:
            confirmed.append(article)
            continue

        # Hard exclusions — content type checks run before any include logic
        if is_sponsored(article):
            skipped["sponsored"] += 1
            continue
        if _is_recap(article):
            skipped["recap"] += 1
            continue
        if _is_non_news(article):
            skipped["non_news"] += 1
            continue

        text = article.raw_text

        # CVSS critical — overrides exclude list
        cvss = _max_cvss(text)
        article.cvss_score = cvss
        if cvss is not None and cvss >= 9.0:
            article.include_reason = "cvss_critical"
            confirmed.append(article)
            continue

        # Keyword/product include match (minus exclude list)
        matched_keyword: str | None = None
        for term in config.include_terms:
            if _term_matches(text, term):
                matched_keyword = term
                break

        if matched_keyword is not None:
            is_excluded = any(_term_matches(text, t) for t in config.exclude_terms)
            if not is_excluded:
                article.include_reason = f"keyword_match:{matched_keyword}"
                confirmed.append(article)
                continue

        # Off-topic guard: only send articles with recognizable infosec content to AI
        if not _has_infosec_content(article):
            skipped["off_topic"] += 1
            continue

        candidates.append(article)

    skip_notes = []
    if skipped["sponsored"]:
        skip_notes.append(f"{skipped['sponsored']} sponsored")
    if skipped["recap"]:
        skip_notes.append(f"{skipped['recap']} recap/digest")
    if skipped["non_news"]:
        skip_notes.append(f"{skipped['non_news']} podcast/video/non-news")
    if skipped["off_topic"]:
        skip_notes.append(f"{skipped['off_topic']} off-topic")
    if skip_notes:
        print(f"  Skipped: {', '.join(skip_notes)}.")

    return confirmed, candidates
