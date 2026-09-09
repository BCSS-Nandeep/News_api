"""
Loads News_URLs.json — the master source registry — and exposes filtered
listings of active sources. This is the only thing that reads that file.

Registry values like `state="Andhra Pradesh & Telangana"` or
`state="Pan-India (Hindi Belt)"` are free-text, so state/language filters use
case-insensitive SUBSTRING matching, not exact equality — a request for
`state=Telangana` must match both.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

_REGISTRY_PATH = Path(__file__).resolve().parent.parent / 'News_URLs.json'


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    region: str
    state: str
    language: str
    type: str
    base_url: str
    active: bool


_sources: Optional[List[Source]] = None


def _load() -> List[Source]:
    global _sources
    if _sources is None:
        with open(_REGISTRY_PATH, encoding='utf-8') as f:
            raw = json.load(f)
        _sources = [Source(**item) for item in raw]
    return _sources


def reload_registry() -> None:
    """Force the next call to re-read News_URLs.json from disk. Mainly for tests."""
    global _sources
    _sources = None


def _contains(haystack: str, needle: str) -> bool:
    return needle.strip().lower() in (haystack or '').lower()


def list_all_sources() -> List[Source]:
    return list(_load())


def list_active_sources(
    state: Optional[str] = None,
    language: Optional[str] = None,
    source_id: Optional[str] = None,
) -> List[Source]:
    """Active sources, optionally narrowed by state/language substring match
    and/or an exact source id. Filters are ANDed together."""
    result = [s for s in _load() if s.active]
    if state:
        result = [s for s in result if _contains(s.state, state)]
    if language:
        result = [s for s in result if _contains(s.language, language)]
    if source_id:
        result = [s for s in result if s.id == source_id or _contains(s.name, source_id)]
    return result


def get_source(source_id: str) -> Optional[Source]:
    for s in _load():
        if s.id == source_id:
            return s
    return None
