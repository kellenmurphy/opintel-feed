from __future__ import annotations

import json
import time

import anthropic

from .cache import CacheManager
from .config import AppConfig
from .models import Article, TokenUsage, ValidationResult

_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_BATCH_SIZE = 10

_SYSTEM_PROMPT = """You are a cybersecurity relevance screener for a university IT security team. \
Your job is to assess whether news articles are relevant to a weekly operational intelligence briefing \
for university IT staff.

Assess each article for relevance based on ANY of these criteria:
1. Specific impact on higher education institutions or academic organizations
2. Geopolitical cybersecurity events with potential impact on US organizations or critical infrastructure
3. Data breaches, ransomware, BEC (Business Email Compromise), or phishing campaigns — \
especially those affecting organizations similar to universities (healthcare, government, education)
4. Well-known threat actors that have targeted higher education, government, or critical infrastructure
5. Cybersecurity vulnerabilities or tools of broad operational importance to enterprise IT teams

When in doubt, exclude. Prefer a shorter list of high-signal articles over a longer list with \
marginal entries.

Mark as NOT relevant:
- Vendor product launch announcements or press releases promoting a commercial product or service, \
even if security-related.
- Articles about niche or highly specialized software products unlikely to be deployed in a US \
university enterprise environment (e.g. industrial control systems, boutique European appliances, \
IoT/embedded platforms).
- Data breaches or incidents affecting only non-US organizations with no direct US impact and no \
known threat actor relevance to US higher education or critical infrastructure.

Respond ONLY with valid JSON — no prose, no markdown, no explanation outside the JSON:
[{"index": 0, "relevant": true, "reason": "one sentence"}, ...]"""


def _build_user_prompt(batch: list[Article]) -> str:
    lines = [f"Evaluate these {len(batch)} articles:"]
    for i, article in enumerate(batch):
        lines.append(f"[{i}] Title: {article.title} | Text: {article.raw_text[:500]}")
    return "\n".join(lines)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _parse_response(
    response_text: str,
    batch: list[Article],
) -> list[ValidationResult]:
    try:
        parsed = json.loads(_strip_code_fence(response_text))
        if not isinstance(parsed, list):
            raise ValueError("Expected a JSON array")

        results: list[ValidationResult] = []
        index_map: dict[int, dict] = {item["index"]: item for item in parsed if "index" in item}

        for i, article in enumerate(batch):
            item = index_map.get(i)
            if item is None:
                results.append(ValidationResult(url=article.url, is_relevant=True, reason="index missing in response"))
            else:
                results.append(
                    ValidationResult(
                        url=article.url,
                        is_relevant=bool(item.get("relevant", True)),
                        reason=str(item.get("reason", "")),
                    )
                )
        return results

    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        print(f"  [WARNING] Haiku returned malformed JSON ({exc}); marking all as relevant. Raw: {response_text[:200]}")
        return [
            ValidationResult(url=a.url, is_relevant=True, reason="parse error — fail-open")
            for a in batch
        ]


def _call_haiku(
    client: anthropic.Anthropic,
    user_prompt: str,
) -> tuple[str, TokenUsage]:
    max_retries = 3
    delay = 2.0
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=_HAIKU_MODEL,
                max_tokens=1024,
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = response.content[0].text if response.content else ""
            usage = response.usage
            token_usage = TokenUsage(
                model=_HAIKU_MODEL,
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


def validate_candidates(
    candidates: list[Article],
    cache: CacheManager,
    config: AppConfig,
    client: anthropic.Anthropic,
    dry_run: bool = False,
    skip_cache_reads: bool = False,
) -> tuple[list[Article], TokenUsage]:
    total_usage = TokenUsage(model=_HAIKU_MODEL)

    if not candidates:
        return [], total_usage

    cached_relevant: list[Article] = []
    uncached: list[Article] = []
    cache_hits = 0

    for article in candidates:
        result = None if skip_cache_reads else cache.get_validation(article.url)
        if result is not None:
            cache_hits += 1
            if result.is_relevant:
                article.include_reason = "ai_validated"
                cached_relevant.append(article)
        else:
            uncached.append(article)

    if cache_hits:
        print(f"  Validation cache: {cache_hits} hits")

    if not uncached:
        return cached_relevant, total_usage

    if len(uncached) > config.max_articles:
        print(
            f"  [WARNING] {len(uncached)} candidates exceed --max-articles cap of {config.max_articles}. "
            f"Processing first {config.max_articles} only."
        )
        uncached = uncached[: config.max_articles]

    if dry_run:
        print(f"  [DRY RUN] Would validate {len(uncached)} articles with {_HAIKU_MODEL}")
        for article in uncached:
            article.include_reason = "ai_validated"
        return cached_relevant + uncached, total_usage

    api_relevant: list[Article] = []
    batches = [uncached[i : i + _BATCH_SIZE] for i in range(0, len(uncached), _BATCH_SIZE)]

    for batch_num, batch in enumerate(batches, 1):
        print(f"  Validating batch {batch_num}/{len(batches)} ({len(batch)} articles)...")
        user_prompt = _build_user_prompt(batch)
        response_text, usage = _call_haiku(client, user_prompt)
        total_usage.add(usage)

        results = _parse_response(response_text, batch)
        for article, result in zip(batch, results):
            cache.set_validation(article.url, result)
            if result.is_relevant:
                article.include_reason = "ai_validated"
                api_relevant.append(article)

    cache.save()
    print(
        f"  Validation complete: {len(api_relevant)}/{len(uncached)} new articles passed "
        f"({len(cached_relevant)} from cache)"
    )
    return cached_relevant + api_relevant, total_usage
