from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import feedparser
import requests
from dateutil import parser as dateutil_parser

from .config import AppConfig, FeedConfig, ManualInclude
from .models import Article

UTM_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term",
    "utm_content", "ref", "referrer",
}

_MAX_TEXT_CHARS = 1500


def normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    query_params = parse_qs(parsed.query, keep_blank_values=False)
    filtered = {k: v for k, v in query_params.items() if k.lower() not in UTM_PARAMS}
    query = urlencode(sorted(filtered.items()), doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


def _url_key(url: str) -> str:
    return hashlib.md5(normalize_url(url).encode()).hexdigest()


def _struct_time_to_datetime(st: time.struct_time | None) -> datetime | None:
    if st is None:
        return None
    try:
        ts = time.mktime(st)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_published(entry: feedparser.FeedParserDict) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        val = getattr(entry, attr, None)
        if val is not None:
            dt = _struct_time_to_datetime(val)
            if dt is not None:
                return dt

    for attr in ("published", "updated", "created"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                dt = dateutil_parser.parse(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except (ValueError, OverflowError):
                continue

    return None


def _truncate(text: str, max_chars: int = _MAX_TEXT_CHARS) -> str:
    return text[:max_chars] if len(text) > max_chars else text


def _entry_to_article(
    entry: feedparser.FeedParserDict,
    feed_url: str,
    published: datetime,
) -> Article | None:
    url_raw = getattr(entry, "link", None) or getattr(entry, "id", None)
    if not url_raw:
        return None
    url = normalize_url(url_raw)
    title = getattr(entry, "title", "").strip() or "(no title)"

    summary = getattr(entry, "summary", "") or ""
    content_list = getattr(entry, "content", [])
    if content_list:
        content_text = content_list[0].get("value", "") if isinstance(content_list[0], dict) else ""
    else:
        content_text = ""

    tags = getattr(entry, "tags", []) or []
    tag_terms = " ".join(
        t.get("term", "") or t.get("label", "")
        for t in tags
        if isinstance(t, dict)
    ).strip()

    body = summary or content_text
    raw_text = _truncate(
        "\n\n".join(part for part in [title, body, tag_terms] if part)
    )

    return Article(
        url=url,
        title=title,
        published=published,
        feed_source=feed_url,
        raw_text=raw_text,
    )


_FEED_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.10 Safari/605.1.1"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}


def _fetch_feed_content(feed_url: str) -> str | None:
    try:
        resp = requests.get(feed_url, timeout=15, headers=_FEED_HEADERS)
        resp.raise_for_status()
        return resp.text
    except requests.HTTPError as exc:
        print(f"  [WARNING] HTTP {exc.response.status_code} fetching feed {feed_url} — skipping.")
        return None
    except requests.RequestException as exc:
        print(f"  [WARNING] Could not fetch feed {feed_url}: {exc}")
        return None


def fetch_all_feeds(config: AppConfig, cutoff: datetime) -> list[Article]:
    seen_keys: dict[str, int] = {}
    articles: list[Article] = []

    for feed_cfg in config.feeds:
        feed_url = feed_cfg.url
        raw_content = _fetch_feed_content(feed_url)
        if raw_content is None:
            continue

        try:
            parsed = feedparser.parse(raw_content)
        except Exception as exc:
            print(f"  [WARNING] Could not parse feed {feed_url}: {exc}")
            continue

        if parsed.get("bozo") and not parsed.get("entries"):
            print(f"  [WARNING] Feed parse error for {feed_url}: {parsed.get('bozo_exception', 'unknown')}")
            continue

        feed_count = 0
        no_date_count = 0
        for entry in parsed.get("entries", []):
            published = _parse_published(entry)
            if published is None:
                no_date_count += 1
                continue
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if published < cutoff:
                continue

            article = _entry_to_article(entry, feed_url, published)
            if article is None:
                continue

            if feed_cfg.max_articles is not None and feed_count >= feed_cfg.max_articles:
                continue

            key = _url_key(article.url)
            if key in seen_keys:
                existing = articles[seen_keys[key]]
                if article.url not in existing.duplicate_urls and article.url != existing.url:
                    existing.duplicate_urls.append(article.url)
            else:
                seen_keys[key] = len(articles)
                articles.append(article)
                feed_count += 1

        no_date_note = f", {no_date_count} skipped (no date)" if no_date_count else ""
        cap_note = f" (capped at {feed_cfg.max_articles})" if feed_cfg.max_articles and feed_count >= feed_cfg.max_articles else ""
        print(f"  {feed_count} new articles from {feed_url}{cap_note}{no_date_note}")

    return articles


def fetch_manual_includes(manual_includes: list[ManualInclude], cutoff: datetime) -> list[Article]:
    articles: list[Article] = []
    for mi in manual_includes:
        url = normalize_url(mi.url)
        title, raw_text = _fetch_article_content(url)
        articles.append(
            Article(
                url=url,
                title=title,
                published=datetime.now(tz=timezone.utc),
                feed_source="manual",
                raw_text=raw_text,
                include_reason="manual",
                is_manual=True,
                manual_category=mi.category,
            )
        )
    return articles


def _fetch_article_content(url: str) -> tuple[str, str]:
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "opintel-feed/1.0"})
        resp.raise_for_status()
        text = resp.text

        title_match = re.search(r"<title[^>]*>([^<]+)</title>", text, re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else "(manual include)"

        clean = re.sub(r"<[^>]+>", " ", text)
        clean = re.sub(r"\s+", " ", clean).strip()
        return title, _truncate(clean)
    except Exception as exc:
        print(f"  [WARNING] Could not fetch manual include {url}: {exc}")
        return "(manual include — title unavailable)", ""
