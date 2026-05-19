from __future__ import annotations

import json
import time
from copy import deepcopy

import anthropic

from .cache import CacheManager
from .config import AppConfig
from .models import Article, TokenUsage

_SONNET_MODEL = "claude-sonnet-4-6"
_BATCH_SIZE = 20

def _build_system_prompt(categories: list[str]) -> str:
    category_list = "\n".join(f"  - {c}" for c in categories)
    return f"""You are a cybersecurity briefing writer for a university IT security team. \
Your job is to create concise summaries of cybersecurity news articles for a weekly operational \
intelligence briefing.

For each article:
- Write 2-4 bullet points (concise, factual, and actionable)
- Assign to exactly one of these categories:
{category_list}

If two or more articles clearly cover the same underlying news event, merge them into a single entry:
- Create a synthesized title that captures the story
- Include ALL of their URLs as a list
- Write a single 2-4 bullet summary

If an article has a provided category hint, use that category.

Do NOT include footnote or endnote reference links in summaries. Do not add citations or \
reference markers like [1] or [^1]. Reference only the information from the article itself.

Respond ONLY with valid JSON — no prose, no markdown, no explanation outside the JSON:
[
  {{
    "title": "Article or synthesized title",
    "urls": ["https://..."],
    "category": "one of the categories above",
    "bullets": ["First bullet point.", "Optional additional bullet point."]
  }}
]"""


def _build_user_prompt(batch: list[Article]) -> str:
    lines = [f"Summarize these {len(batch)} articles:"]
    for i, article in enumerate(batch):
        category_hint = article.manual_category or "auto"
        lines.append(
            f"[{i}] Title: {article.title} | URL: {article.url} | "
            f"Category hint: {category_hint} | Text: {article.raw_text}"
        )
    return "\n\n".join(lines)


def _dedup_summarized(results: list[Article]) -> list[Article]:
    covered: set[str] = set()
    for r in results:
        if r.duplicate_urls:
            for u in r.all_urls:
                covered.add(u)
    return [r for r in results if r.duplicate_urls or r.url not in covered]


def _parse_response(response_text: str, batch: list[Article], categories: list[str]) -> list[Article]:
    try:
        text = response_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        parsed = json.loads(text.strip())
        if not isinstance(parsed, list):
            raise ValueError("Expected a JSON array")

        results: list[Article] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue

            urls: list[str] = item.get("urls", [])
            if not urls:
                continue

            primary_url = urls[0]
            duplicate_urls = urls[1:]

            source_article = next(
                (a for a in batch if a.url == primary_url),
                None,
            )
            if source_article is None:
                source_article = next(
                    (a for a in batch if a.url in urls),
                    None,
                )

            if source_article is not None:
                result = deepcopy(source_article)
            else:
                result = Article(
                    url=primary_url,
                    title=item.get("title", "(unknown)"),
                    published=batch[0].published if batch else __import__("datetime").datetime.now(),
                    feed_source="merged",
                    raw_text="",
                )

            result.title = item.get("title", result.title)
            result.url = primary_url
            result.duplicate_urls = duplicate_urls

            raw_category = item.get("category", categories[-1])
            result.category = raw_category if raw_category in categories else categories[-1]

            bullets = item.get("bullets", [])
            result.summary_bullets = [str(b) for b in bullets if b]
            if not result.summary_bullets:
                result.summary_bullets = ["Summary unavailable — manual review required."]

            results.append(result)

        return _dedup_summarized(results)

    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        print(
            f"  [WARNING] Sonnet returned malformed JSON ({exc}); "
            f"applying fallback summaries. Raw: {response_text[:200]}"
        )
        fallbacks: list[Article] = []
        for article in batch:
            a = deepcopy(article)
            a.summary_bullets = ["Summary unavailable — manual review required."]
            a.category = article.manual_category or categories[-1]
            fallbacks.append(a)
        return fallbacks


def _call_sonnet(
    client: anthropic.Anthropic,
    user_prompt: str,
    system_prompt: str,
) -> tuple[str, TokenUsage]:
    max_retries = 3
    delay = 2.0
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=_SONNET_MODEL,
                max_tokens=4096,
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = response.content[0].text if response.content else ""
            usage = response.usage
            token_usage = TokenUsage(
                model=_SONNET_MODEL,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            )
            return text, token_usage
        except anthropic.RateLimitError:
            if attempt < max_retries - 1:
                print(f"  [WARNING] Rate limit hit; retrying in {delay:.0f}s...")
                time.sleep(delay)
                delay *= 2
            else:
                raise
        except anthropic.APIStatusError as exc:
            if attempt < max_retries - 1:
                print(f"  [WARNING] API error ({exc.status_code}); retrying in {delay:.0f}s...")
                time.sleep(delay)
                delay *= 2
            else:
                raise
    raise RuntimeError(f"_call_sonnet exhausted {max_retries} retries without returning")


def summarize_articles(
    articles: list[Article],
    cache: CacheManager,
    config: AppConfig,
    client: anthropic.Anthropic,
    dry_run: bool = False,
    skip_cache_reads: bool = False,
) -> tuple[list[Article], TokenUsage]:
    total_usage = TokenUsage(model=_SONNET_MODEL)

    if not articles:
        return [], total_usage

    cached_results: list[Article] = []
    uncached: list[Article] = []
    cache_hits = 0

    for article in articles:
        cached = None if skip_cache_reads else cache.get_summary(article.url)
        if cached is not None:
            cache_hits += 1
            a = deepcopy(article)
            a.title = cached.get("title", article.title)
            a.category = cached.get("category", config.categories[-1])
            a.summary_bullets = cached.get("bullets", ["Summary unavailable — manual review required."])
            extra_urls = cached.get("urls", [article.url])
            a.duplicate_urls = [u for u in extra_urls if u != a.url]
            cached_results.append(a)
        else:
            uncached.append(article)

    if cache_hits:
        print(f"  Summarization cache: {cache_hits} hits")

    if not uncached:
        return cached_results, total_usage

    if dry_run:
        print(f"  [DRY RUN] Would summarize {len(uncached)} articles with {_SONNET_MODEL}")
        for article in uncached:
            article.summary_bullets = ["[Dry run — summary not generated]"]
            article.category = article.manual_category or config.categories[-1]
        return cached_results + uncached, total_usage

    system_prompt = _build_system_prompt(config.categories)
    summarized: list[Article] = []
    batches = [uncached[i : i + _BATCH_SIZE] for i in range(0, len(uncached), _BATCH_SIZE)]

    for batch_num, batch in enumerate(batches, 1):
        print(f"  Summarizing batch {batch_num}/{len(batches)} ({len(batch)} articles)...")
        user_prompt = _build_user_prompt(batch)
        response_text, usage = _call_sonnet(client, user_prompt, system_prompt)
        total_usage.add(usage)

        results = _parse_response(response_text, batch, config.categories)

        for result in results:
            cache.set_summary(
                result.url,
                {
                    "title": result.title,
                    "category": result.category,
                    "bullets": result.summary_bullets,
                    "urls": result.all_urls,
                },
            )
        summarized.extend(results)

    cache.save()
    print(f"  Summarization complete: {len(summarized)} entries generated ({cache_hits} from cache)")
    return cached_results + summarized, total_usage
