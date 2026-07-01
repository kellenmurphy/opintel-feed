#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Model pricing — USD per token. Single source of truth for both the dry-run
# estimator and the post-run token report.
# Source: https://platform.claude.com/docs/en/pricing  (verified 2026-07-01)
#   Haiku 4.5 : $1.00 in / $5.00 out / $0.10 cache-read / $1.25 cache-write per MTok
#   Sonnet 5  : $3.00 in / $15.00 out / $0.30 cache-read / $3.75 cache-write per MTok
#               (standard rates; Sonnet 5 intro pricing of $2/$10 through 2026-08-31
#               means real cost is lower, so these bias the estimate upward.)
# ---------------------------------------------------------------------------
_PRICING = {
    tier: {k: v / 1_000_000 for k, v in rates.items()}
    for tier, rates in {
        "haiku": {"in": 1.00, "out": 5.00, "cache_read": 0.10, "cache_write": 1.25},
        "sonnet": {"in": 3.00, "out": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    }.items()
}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="opintel-feed",
        description="Generate an Operational Intelligence cybersecurity briefing document.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip all API calls; print the article list that would be processed and a worst-case cost estimate, then exit.",
    )
    p.add_argument(
        "--config-dir",
        type=Path,
        default=Path("config"),
        metavar="PATH",
        help="Directory containing feeds.json, include.json, exclude.json, manual_includes.json. (default: config/)",
    )
    p.add_argument(
        "--format",
        nargs="+",
        choices=["html", "md", "txt"],
        default=["html"],
        metavar="FMT",
        help="Output format(s): html, md, txt. Multiple values are allowed. (default: html)",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Output file path. With a single --format, used as-is. "
            "With multiple formats, the extension is replaced per format. "
            "(default: output/briefing_YYYY-MM-DD.<ext>)"
        ),
    )
    p.add_argument(
        "--max-articles",
        type=int,
        default=None,
        metavar="N",
        help="Cap on candidate articles sent to AI validation. (default: 50)",
    )
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("cache"),
        metavar="PATH",
        help="Directory for the disk cache. (default: cache/)",
    )
    p.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable cache reads (will still write new results to cache).",
    )
    p.add_argument(
        "--days",
        type=int,
        default=7,
        metavar="N",
        help="Lookback window in days for article freshness. (default: 7)",
    )
    p.add_argument(
        "--prior-days",
        type=int,
        default=None,
        metavar="N",
        help=(
            "How many days back to fetch for prior-week overlap detection. "
            "Defaults to the same value as --days, giving a 2x fetch window. "
            "Set to 0 to disable overlap detection entirely. (default: same as --days)"
        ),
    )
    p.add_argument(
        "--skip-preview",
        action="store_true",
        help=(
            "Skip the post-validation article preview and proceed directly to Sonnet "
            "summarization without prompting."
        ),
    )
    p.add_argument(
        "--preview-file",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Write the post-validation article list to PATH before starting Sonnet "
            "summarization, then continue automatically. Use --skip-preview to suppress the interactive prompt while still writing the file."
        ),
    )
    return p


def _estimate_dry_run_cost(n_validate: int, n_summarize: int) -> None:
    # Approximate token counts per article at each phase
    HAIKU_SYS_TOKENS  = 250   # system prompt (cached after batch 1)
    HAIKU_IN_PER      = 150   # title + 500-char text snippet per article
    HAIKU_OUT_PER     = 30    # JSON result per article
    HAIKU_BATCH       = 10

    SONNET_SYS_TOKENS = 400   # system prompt (cached after batch 1)
    SONNET_IN_PER     = 420   # title + URL + 1500-char text per article
    SONNET_OUT_PER    = 260   # ~2 bullets + category + adaptive-thinking tokens
    SONNET_BATCH      = 20

    # Bias the estimate upward: prefer a slight overestimate to underestimating.
    SAFETY_MARGIN = 1.15

    haiku = _PRICING["haiku"]
    sonnet = _PRICING["sonnet"]

    h_batches = math.ceil(n_validate  / HAIKU_BATCH)  if n_validate  else 0
    s_batches = math.ceil(n_summarize / SONNET_BATCH) if n_summarize else 0

    haiku_cost = (
        HAIKU_IN_PER  * n_validate  * haiku["in"]
      + HAIKU_OUT_PER * n_validate  * haiku["out"]
      + HAIKU_SYS_TOKENS            * haiku["cache_write"]                          # cache write (first batch)
      + HAIKU_SYS_TOKENS            * haiku["cache_read"] * max(0, h_batches - 1)   # cache reads (subsequent)
    )
    sonnet_cost = (
        SONNET_IN_PER  * n_summarize * sonnet["in"]
      + SONNET_OUT_PER * n_summarize * sonnet["out"]
      + SONNET_SYS_TOKENS            * sonnet["cache_write"]
      + SONNET_SYS_TOKENS            * sonnet["cache_read"] * max(0, s_batches - 1)
    )
    haiku_cost *= SAFETY_MARGIN
    sonnet_cost *= SAFETY_MARGIN
    total = haiku_cost + sonnet_cost

    print(f"\n{'─' * 58}")
    print(f"  Estimated API cost  (worst case: all candidates pass)")
    print(f"  includes {round((SAFETY_MARGIN - 1) * 100)}% safety margin")
    print(f"{'─' * 58}")
    print(
        f"  Haiku   {n_validate:>3} articles × {h_batches} batch{'es' if h_batches != 1 else ' '}    ~${haiku_cost:.4f}"
    )
    print(
        f"  Sonnet  {n_summarize:>3} articles × {s_batches} batch{'es' if s_batches != 1 else ' '}    ~${sonnet_cost:.4f}"
    )
    print(f"  {'─' * 40}")
    print(f"  Total                              ~${total:.4f}")
    print(f"{'─' * 58}")


def _append_to_previous_includes(path: Path, articles: list) -> None:
    existing: list[dict] = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = []
    existing_urls = {e["url"] for e in existing if "url" in e}
    added = 0
    for article in articles:
        if article.url not in existing_urls:
            existing.append({"url": article.url, "title": article.title})
            existing_urls.add(article.url)
            added += 1
    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if added:
        print(f"  Updated {path}: added {added} new article(s) to previous includes.")


def _format_preview(articles: list) -> str:
    lines = [f"Post-validation article list ({len(articles)} articles)\n"]
    for i, a in enumerate(articles, 1):
        reason = a.include_reason or "candidate"
        lines.append(f"{i:>3}. [{reason}] {a.title}")
        lines.append(f"       {a.url}")
    return "\n".join(lines)


def _run_preview_gate(articles: list, preview_file, interactive: bool) -> None:
    """Print/save the post-validation article list and optionally prompt to continue."""
    text = _format_preview(articles)

    if preview_file:
        preview_file = Path(preview_file)
        preview_file.write_text(text, encoding="utf-8")
        print(f"  Preview written to: {preview_file}")

    if not interactive:
        return

    print(f"\n{text}\n")

    while True:
        try:
            answer = input("Proceed with summarization? [Y/n/filename]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(0)

        if answer == "" or answer.lower() == "y":
            return
        if answer.lower() == "n":
            print("Aborted.")
            sys.exit(0)
        # treat any other input as a file path
        try:
            out = Path(answer)
            out.write_text(text, encoding="utf-8")
            print(f"  Preview written to: {out}")
            return
        except OSError as exc:
            print(f"  Could not write '{answer}': {exc}. Enter Y, n, or a valid path.")


def _print_token_report(usages: list) -> None:
    if not usages:
        return

    def cost(u) -> float:
        rates = _PRICING["haiku"] if "haiku" in u.model.lower() else _PRICING["sonnet"]
        return (
            u.input_tokens * rates["in"]
            + u.output_tokens * rates["out"]
            + u.cache_read_tokens * rates["cache_read"]
            + u.cache_write_tokens * rates["cache_write"]
        )

    print("\n" + "=" * 72)
    print(f"{'Model':<35} {'Input':>7} {'Output':>7} {'CacheRd':>8} {'CacheWr':>8}")
    print("-" * 72)
    total_cost = 0.0
    for u in usages:
        c = cost(u)
        total_cost += c
        print(
            f"{u.model:<35} {u.input_tokens:>7,} {u.output_tokens:>7,} "
            f"{u.cache_read_tokens:>8,} {u.cache_write_tokens:>8,}"
        )
    print("-" * 72)
    print(f"{'Estimated cost':<35} ${total_cost:.4f}")
    print("=" * 72)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # ------------------------------------------------------------------ imports
    from opintel.cache import CacheManager
    from opintel.config import ConfigError, load_config
    from opintel.fetcher import fetch_all_feeds, fetch_manual_includes
    from opintel.models import TokenUsage
    from opintel.prefilter import deduplicate_final_pool, filter_previous_includes, filter_prior_window_overlaps, prefilter_articles
    from opintel.renderer import (
        output_path_for_format,
        render_html,
        render_markdown,
        render_text,
        unique_output_path,
    )
    from opintel.summarizer import summarize_articles
    from opintel.validator import validate_candidates

    # ------------------------------------------------------------------ 1. SETUP
    print("=" * 60)
    print("  OpIntel Feed — Briefing Generator")
    print("=" * 60)

    try:
        config = load_config(args.config_dir)
    except ConfigError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    if args.max_articles is not None:
        config.max_articles = args.max_articles
    config.cache_dir = args.cache_dir
    config.lookback_days = args.days

    prev_count = len(config.previous_includes)
    prev_note = f", {prev_count} previous includes" if prev_count else ""
    print(
        f"\nLoaded {len(config.feeds)} feeds, "
        f"{len(config.include_terms)} include keywords, "
        f"{len(config.exclude_terms)} exclude keywords, "
        f"{len(config.manual_includes)} manual includes{prev_note}."
    )

    cache = CacheManager(args.cache_dir, ttl_days=config.cache_ttl_days)
    if not args.no_cache:
        evicted = cache.evict_expired()
        if evicted:
            print(f"  Cache: evicted {evicted} expired entries.")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key and not args.dry_run:
        print("[ERROR] ANTHROPIC_API_KEY is not set. Set it in .env or the environment.")
        sys.exit(1)

    client = None
    if not args.dry_run:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

    # ------------------------------------------------------------------ 2. FETCH
    now = datetime.now(tz=timezone.utc)
    cutoff = now - timedelta(days=args.days)
    prior_days = args.prior_days if args.prior_days is not None else args.days
    prior_cutoff = cutoff - timedelta(days=prior_days)

    if prior_days > 0:
        print(f"\nFetching articles published after {prior_cutoff.strftime('%Y-%m-%d %H:%M UTC')} ({args.days}d current + {prior_days}d prior overlap window)...")
    else:
        print(f"\nFetching articles published after {cutoff.strftime('%Y-%m-%d %H:%M UTC')} (overlap detection disabled)...")

    all_fetched = fetch_all_feeds(config, prior_cutoff if prior_days > 0 else cutoff)

    current_articles = [a for a in all_fetched if a.published >= cutoff]
    prior_articles = [a for a in all_fetched if a.published < cutoff] if prior_days > 0 else []
    if prior_days > 0:
        print(
            f"  Current window: {len(current_articles)} articles, "
            f"prior window: {len(prior_articles)} articles."
        )
    else:
        print(f"  Current window: {len(current_articles)} articles.")

    manual_articles = fetch_manual_includes(config.manual_includes, cutoff)
    if manual_articles:
        print(f"  Manual includes: {len(manual_articles)} articles.")

    all_articles = current_articles + manual_articles

    # ------------------------------------------------------------------ 3. PRE-FILTER
    print("\nRunning heuristic pre-filter...")
    all_articles = filter_prior_window_overlaps(all_articles, prior_articles)
    all_articles = filter_previous_includes(all_articles, config.previous_includes)
    confirmed, candidates = prefilter_articles(all_articles, config)
    print(
        f"  Pre-filter: {len(confirmed)} confirmed "
        f"(keyword match / CVSS ≥ 9.0 / manual), "
        f"{len(candidates)} candidates for AI validation."
    )

    token_usages: list[TokenUsage] = []

    # ------------------------------------------------------------------ 4. PHASE 1 — HAIKU
    print("\nPhase 1: AI relevance validation (Haiku)...")
    validated_candidates, haiku_usage = validate_candidates(
        candidates, cache, config, client,
        dry_run=args.dry_run,
        skip_cache_reads=args.no_cache,
    )
    if haiku_usage.input_tokens or haiku_usage.cache_read_tokens:
        token_usages.append(haiku_usage)

    # ------------------------------------------------------------------ 5. MERGE
    final_articles = confirmed + validated_candidates
    final_articles = deduplicate_final_pool(final_articles)
    print(f"\nFinal article pool: {len(final_articles)} articles for summarization.")

    if args.dry_run:
        print("\n[DRY RUN] Articles that would be summarized:")
        for i, a in enumerate(final_articles, 1):
            print(f"  {i:>3}. [{a.include_reason or 'candidate'}] {a.title}")
            print(f"       {a.url}")
        _estimate_dry_run_cost(
            n_validate=len(candidates),
            n_summarize=len(final_articles),
        )
        print("\nDry run complete. No API calls were made.")
        return

    if not args.skip_preview or args.preview_file:
        _run_preview_gate(
            final_articles,
            preview_file=args.preview_file,
            interactive=not args.skip_preview,
        )

    if not final_articles:
        print("\nNo articles met inclusion criteria. Writing empty briefing.")

    # ------------------------------------------------------------------ 6. PHASE 2 — SONNET
    print("\nPhase 2: Summarization (Sonnet)...")
    summarized, sonnet_usage = summarize_articles(
        final_articles, cache, config, client,
        dry_run=False,
        skip_cache_reads=args.no_cache,
    )
    if sonnet_usage.input_tokens or sonnet_usage.cache_read_tokens:
        token_usages.append(sonnet_usage)

    # ------------------------------------------------------------------ 7. RENDER
    _renderers = {"html": render_html, "md": render_markdown, "txt": render_text}
    base_date = now.strftime("%Y-%m-%d")
    single_format = len(args.format) == 1

    for fmt in args.format:
        path = output_path_for_format(fmt, base_date, args.output, single_format)
        path = unique_output_path(path)
        _renderers[fmt](summarized, path, run_date=now, lookback_days=args.days, categories=config.categories)
        print(f"\nOutput written to: {path}")

    # ------------------------------------------------------------------ 8. PREVIOUS INCLUDES
    _append_to_previous_includes(config.previous_includes_path, summarized)

    # ------------------------------------------------------------------ 9. REPORT
    _print_token_report(token_usages)


if __name__ == "__main__":
    main()
