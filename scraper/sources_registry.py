"""
Loads News_URLs.json — the master source registry — and exposes filtered
listings of active sources. This is the only thing that reads that file.

Registry values like `state="Andhra Pradesh & Telangana"` or
`state="Pan-India (Hindi Belt)"` are free-text, so country/state/language
filters use case-insensitive SUBSTRING matching, not exact equality — a
request for `state=Telangana` must match both.

Every filter is MULTI-VALUE: the frontend renders checkboxes, so a request
can carry `country=India,United States`. Values within one filter are ORed,
different filters are ANDed — see parse_multi() and list_active_sources().
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Union

_REGISTRY_PATH = Path(__file__).resolve().parent.parent / 'News_URLs.json'

# A single filter value, a comma-separated string of them, or a list of them.
FilterValue = Optional[Union[str, Sequence[str]]]


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
    # Defaulted so a registry entry (or a test fixture) written before country
    # existed still loads — every entry in News_URLs.json now sets it.
    country: str = ''


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


def parse_multi(value: FilterValue) -> List[str]:
    """Normalize a checkbox-style filter into a list of needles.

    Accepts what a query string actually delivers: `None`, `"India"`,
    `"India, United States"`, or an already-split list. Comma is the separator
    because that is what a frontend gets for free from a checkbox group's
    selected values, and no registry country/state/language value contains one.
    Whitespace is trimmed and empty selections are dropped, so a stray
    `?country=` or `?country=India,,` never silently filters everything out.
    """
    if value is None:
        return []
    if isinstance(value, str):
        parts: Iterable[str] = value.split(',')
    else:
        # A list from FastAPI's repeated-param form (?country=A&country=B);
        # each element may itself still be comma-separated.
        parts = (piece for item in value for piece in str(item).split(','))
    return [p.strip() for p in parts if p and p.strip()]


def _contains(haystack: str, needle: str) -> bool:
    return needle.strip().lower() in (haystack or '').lower()


def _matches_any(haystack: str, needles: Sequence[str]) -> bool:
    """OR within one filter: no needles means "filter not applied"."""
    if not needles:
        return True
    return any(_contains(haystack, n) for n in needles)


def _matches_source_ref(source: 'Source', needles: Sequence[str]) -> bool:
    """`source=` accepts a registry id (preferred — stable) or a name substring."""
    if not needles:
        return True
    return any(source.id == n.strip() or _contains(source.name, n) for n in needles)


def list_all_sources() -> List[Source]:
    return list(_load())


def list_active_sources(
    country: FilterValue = None,
    state: FilterValue = None,
    language: FilterValue = None,
    source_id: FilterValue = None,
) -> List[Source]:
    """Active sources, narrowed by country/state/language substring match
    and/or source ids (or name substrings).

    Each filter accepts one value or many ("India,United States"). Values
    inside one filter are ORed; separate filters are ANDed. `country` is
    first, but every caller passes keywords, and the pre-existing
    state/language/source_id keywords keep working unchanged.
    """
    countries = parse_multi(country)
    states = parse_multi(state)
    languages = parse_multi(language)
    source_refs = parse_multi(source_id)

    return [
        s for s in _load()
        if s.active
        and _matches_any(s.country, countries)
        and _matches_any(s.state, states)
        and _matches_any(s.language, languages)
        and _matches_source_ref(s, source_refs)
    ]


def get_source(source_id: str) -> Optional[Source]:
    for s in _load():
        if s.id == source_id:
            return s
    return None
