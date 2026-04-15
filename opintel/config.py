from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .models import CATEGORIES


class ConfigError(Exception):
    pass


@dataclass
class FeedConfig:
    url: str
    max_articles: int | None = None


@dataclass
class ManualInclude:
    url: str
    category: str | None = None


@dataclass
class AppConfig:
    feeds: list[FeedConfig]
    include_terms: list[str]
    exclude_terms: list[str]
    manual_includes: list[ManualInclude]
    max_articles: int = 50
    cache_ttl_days: int = 7
    output_dir: Path = Path("output")
    cache_dir: Path = Path("cache")
    lookback_days: int = 7


def _load_json(path: Path, name: str) -> object:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}  (expected {name})")
    try:
        with path.open() as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc


def _parse_feeds(feeds_raw: list) -> list[FeedConfig]:
    feeds: list[FeedConfig] = []
    for i, entry in enumerate(feeds_raw):
        if isinstance(entry, str):
            feeds.append(FeedConfig(url=entry))
        elif isinstance(entry, dict) and "url" in entry:
            max_a = entry.get("max")
            if max_a is not None and not isinstance(max_a, int):
                raise ConfigError(f"feeds.json entry {i}: 'max' must be an integer")
            feeds.append(FeedConfig(url=entry["url"], max_articles=max_a))
        else:
            raise ConfigError(
                f"feeds.json entry {i} must be a URL string or "
                f"an object with a 'url' field (and optional 'max')"
            )
    return feeds


def load_config(config_dir: Path) -> AppConfig:
    feeds_raw = _load_json(config_dir / "feeds.json", "feeds.json")
    if not isinstance(feeds_raw, list):
        raise ConfigError("feeds.json must be a JSON array")
    feeds = _parse_feeds(feeds_raw)

    include_raw = _load_json(config_dir / "include.json", "include.json")
    if not isinstance(include_raw, list) or not all(isinstance(t, str) for t in include_raw):
        raise ConfigError("include.json must be a JSON array of strings")

    exclude_raw = _load_json(config_dir / "exclude.json", "exclude.json")
    if not isinstance(exclude_raw, list) or not all(isinstance(t, str) for t in exclude_raw):
        raise ConfigError("exclude.json must be a JSON array of strings")

    manual_raw = _load_json(config_dir / "manual_includes.json", "manual_includes.json")
    if not isinstance(manual_raw, list):
        raise ConfigError("manual_includes.json must be a JSON array")

    manual_includes: list[ManualInclude] = []
    for i, item in enumerate(manual_raw):
        if not isinstance(item, dict) or "url" not in item:
            raise ConfigError(
                f"manual_includes.json entry {i} must be an object with a 'url' field"
            )
        category = item.get("category")
        if category is not None and category not in CATEGORIES:
            raise ConfigError(
                f"manual_includes.json entry {i} has unknown category '{category}'. "
                f"Valid values: {CATEGORIES}"
            )
        manual_includes.append(ManualInclude(url=item["url"], category=category))

    max_articles = int(os.environ.get("OPINTEL_MAX_ARTICLES", "50"))
    cache_ttl_days = int(os.environ.get("OPINTEL_CACHE_TTL_DAYS", "7"))

    return AppConfig(
        feeds=feeds,
        include_terms=include_raw,
        exclude_terms=exclude_raw,
        manual_includes=manual_includes,
        max_articles=max_articles,
        cache_ttl_days=cache_ttl_days,
    )
