from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


CATEGORIES = [
    "Patching/Security Concerns",
    "Malware/Ransomware/BEC/Scams",
    "Nation States and GeoPolitics",
    "Other News",
]


@dataclass
class Article:
    url: str
    title: str
    published: datetime
    feed_source: str
    raw_text: str
    cvss_score: float | None = None
    include_reason: str | None = None
    category: str | None = None
    summary_bullets: list[str] = field(default_factory=list)
    duplicate_urls: list[str] = field(default_factory=list)
    is_manual: bool = False
    manual_category: str | None = None

    @property
    def all_urls(self) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for u in [self.url] + self.duplicate_urls:
            if u not in seen:
                seen.add(u)
                result.append(u)
        return result


@dataclass
class ValidationResult:
    url: str
    is_relevant: bool
    reason: str
    cached: bool = False


@dataclass
class TokenUsage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, other: TokenUsage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
