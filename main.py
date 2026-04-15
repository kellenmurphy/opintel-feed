#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


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
        "--output",
        type=Path,
        default=None,
        metavar="PATH",
        help="Output HTML file path. (default: output/briefing_YYYY-MM-DD.html)",
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
    return p


def _estimate_dry_run_cost(n_validate: int, n_summarize: int) -> None:
    # Approximate token counts per article at each phase
    HAIKU_SYS_TOKENS  = 250   # system prompt (cached after batch 1)
    HAIKU_IN_PER      = 150   # title + 500-char text snippet per article
    HAIKU_OUT_PER     = 30    # JSON result per article
    HAIKU_BATCH       = 10

    SONNET_SYS_TOKENS = 400   # system prompt (cached after batch 1)
    SONNET_IN_PER     = 420   # title + URL + 1500-char text per article
    SONNET_OUT_PER    = 120   # ~2 bullets + category per article
    SONNET_BATCH      = 20

    # Pricing (per token)
    HAIKU_IN   = 0.80  / 1_000_000
    HAIKU_OUT  = 4.00  / 1_000_000
    HAIKU_CR   = 0.08  / 1_000_000
    HAIKU_CW   = 1.00  / 1_000_000
    SONNET_IN  = 3.00  / 1_000_000
    SONNET_OUT = 15.00 / 1_000_000
    SONNET_CR  = 0.30  / 1_000_000
    SONNET_CW  = 3.75  / 1_000_000

    h_batches = math.ceil(n_validate  / HAIKU_BATCH)  if n_validate  else 0
    s_batches = math.ceil(n_summarize / SONNET_BATCH) if n_summarize else 0

    haiku_cost = (
        HAIKU_IN_PER  * n_validate  * HAIKU_IN
      + HAIKU_OUT_PER * n_validate  * HAIKU_OUT
      + HAIKU_SYS_TOKENS            * HAIKU_CW                          # cache write (first batch)
      + HAIKU_SYS_TOKENS            * HAIKU_CR * max(0, h_batches - 1)  # cache reads (subsequent)
    )
    sonnet_cost = (
        SONNET_IN_PER  * n_summarize * SONNET_IN
      + SONNET_OUT_PER * n_summarize * SONNET_OUT
      + SONNET_SYS_TOKENS            * SONNET_CW
      + SONNET_SYS_TOKENS            * SONNET_CR * max(0, s_batches - 1)
    )
    total = haiku_cost + sonnet_cost

    print(f"\n{'─' * 58}")
    print(f"  Estimated API cost  (worst case: all candidates pass)")
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


def _print_token_report(usages: list) -> None:
    if not usages:
        return

    HAIKU_IN = 0.80 / 1_000_000
    HAIKU_OUT = 4.00 / 1_000_000
    HAIKU_CACHE_READ = 0.08 / 1_000_000
    HAIKU_CACHE_WRITE = 1.00 / 1_000_000
    SONNET_IN = 3.00 / 1_000_000
    SONNET_OUT = 15.00 / 1_000_000
    SONNET_CACHE_READ = 0.30 / 1_000_000
    SONNET_CACHE_WRITE = 3.75 / 1_000_000

    def cost(u) -> float:
        if "haiku" in u.model.lower():
            return (
                u.input_tokens * HAIKU_IN
                + u.output_tokens * HAIKU_OUT
                + u.cache_read_tokens * HAIKU_CACHE_READ
                + u.cache_write_tokens * HAIKU_CACHE_WRITE
            )
        else:
            return (
                u.input_tokens * SONNET_IN
                + u.output_tokens * SONNET_OUT
                + u.cache_read_tokens * SONNET_CACHE_READ
                + u.cache_write_tokens * SONNET_CACHE_WRITE
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
    from opintel.prefilter import filter_prior_window_overlaps, prefilter_articles
    from opintel.renderer import render_briefing, unique_output_path
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

    print(
        f"\nLoaded {len(config.feeds)} feeds, "
        f"{len(config.include_terms)} include keywords, "
        f"{len(config.exclude_terms)} exclude keywords, "
        f"{len(config.manual_includes)} manual includes."
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
    prior_cutoff = cutoff - timedelta(days=args.days)
    print(f"\nFetching articles published after {prior_cutoff.strftime('%Y-%m-%d %H:%M UTC')} (2x window for overlap detection)...")

    all_fetched = fetch_all_feeds(config, prior_cutoff)

    current_articles = [a for a in all_fetched if a.published >= cutoff]
    prior_articles = [a for a in all_fetched if a.published < cutoff]
    print(
        f"  Current window: {len(current_articles)} articles, "
        f"prior window: {len(prior_articles)} articles."
    )

    manual_articles = fetch_manual_includes(config.manual_includes, cutoff)
    if manual_articles:
        print(f"  Manual includes: {len(manual_articles)} articles.")

    all_articles = current_articles + manual_articles

    # ------------------------------------------------------------------ 3. PRE-FILTER
    print("\nRunning heuristic pre-filter...")
    all_articles = filter_prior_window_overlaps(all_articles, prior_articles)
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
    if args.output:
        output_path = args.output
    else:
        output_dir = Path("output")
        output_path = output_dir / f"briefing_{now.strftime('%Y-%m-%d')}.html"

    output_path = unique_output_path(output_path)
    render_briefing(summarized, output_path, run_date=now, lookback_days=args.days)
    print(f"\nOutput written to: {output_path}")

    # ------------------------------------------------------------------ 8. REPORT
    _print_token_report(token_usages)


if __name__ == "__main__":
    main()
