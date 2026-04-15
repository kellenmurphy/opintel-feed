from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import ValidationResult


def _url_key(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


class CacheManager:
    def __init__(self, cache_dir: Path, ttl_days: int = 7) -> None:
        self._path = cache_dir / "cache.json"
        self._ttl = timedelta(days=ttl_days)
        self._data: dict[str, Any] = {"version": 1, "entries": {}}
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open() as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict) and loaded.get("version") == 1:
                self._data = loaded
        except (json.JSONDecodeError, OSError):
            print("  [WARNING] Cache file is corrupt; starting with empty cache.")
            self._data = {"version": 1, "entries": {}}

    def save(self) -> None:
        parent = self._path.parent
        try:
            fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".tmp")
            with os.fdopen(fd, "w") as fh:
                json.dump(self._data, fh, indent=2)
            os.replace(tmp_path, self._path)
        except OSError as exc:
            print(f"  [WARNING] Could not save cache: {exc}")

    def evict_expired(self) -> int:
        now = datetime.now(tz=timezone.utc)
        entries = self._data["entries"]
        expired = [
            k for k, v in entries.items()
            if self._is_expired(v, now)
        ]
        for k in expired:
            del entries[k]
        if expired:
            self.save()
        return len(expired)

    def _is_expired(self, entry: dict, now: datetime) -> bool:
        try:
            cached_at = datetime.fromisoformat(entry["cached_at"])
            ttl_days = entry.get("ttl_days", self._ttl.days)
            return (now - cached_at) > timedelta(days=ttl_days)
        except (KeyError, ValueError):
            return True

    def _get_entry(self, key: str) -> dict | None:
        entry = self._data["entries"].get(key)
        if entry is None:
            return None
        if self._is_expired(entry, datetime.now(tz=timezone.utc)):
            return None
        return entry

    def get_validation(self, url: str) -> ValidationResult | None:
        key = _url_key(url)
        entry = self._get_entry(key)
        if entry is None or entry.get("type") != "validation":
            return None
        data = entry["data"]
        return ValidationResult(
            url=url,
            is_relevant=data["is_relevant"],
            reason=data.get("reason", ""),
            cached=True,
        )

    def set_validation(self, url: str, result: ValidationResult) -> None:
        key = _url_key(url)
        self._data["entries"][key] = {
            "url": url,
            "cached_at": datetime.now(tz=timezone.utc).isoformat(),
            "ttl_days": self._ttl.days,
            "type": "validation",
            "data": {
                "is_relevant": result.is_relevant,
                "reason": result.reason,
            },
        }

    def get_summary(self, url: str) -> dict | None:
        key = _url_key(url) + "_summary"
        entry = self._get_entry(key)
        if entry is None or entry.get("type") != "summary":
            return None
        return entry["data"]

    def set_summary(self, url: str, data: dict) -> None:
        key = _url_key(url) + "_summary"
        self._data["entries"][key] = {
            "url": url,
            "cached_at": datetime.now(tz=timezone.utc).isoformat(),
            "ttl_days": self._ttl.days,
            "type": "summary",
            "data": data,
        }
