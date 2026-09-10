"""
Orchestrates the whole DISCOVER -> SCRAPE -> NORMALIZE -> FILTER -> PAGINATE
flow for the News API. This is the only place that ties scraper/, processing/
and services/cache together — routes call this, never the lower layers
directly (see api/routes/articles.py).

Every filter is multi-value (the frontend uses checkboxes): values within one
filter are ORed, different filters are ANDed. Registry-backed filters
(country/state/language/source) also narrow which sources get scraped at all,
so a request for `country=Japan` never touches the Indian sources.
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from scraper import discovery
from scraper.extraction import fetch_article_page, prefer_https
from scraper.normalize import make_article_id, dedupe_by_id
from scraper.sources_registry import (
    FilterValue,
    Source,
    list_active_sources,
    list_all_sources,
    parse_multi,
)
from processing.location import resolve_location
from processing.keywords import matches_keyword
from services import cache

_MAX_SOURCES_PER_REQUEST = int(os.getenv('MAX_SOURCES_PER_REQUEST', '40'))
_MAX_WORKERS = int(os.getenv('DISCOVERY_MAX_WORKERS', '10'))

# Sort fallback for an article with no usable timestamp — sorts last.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Force a datetime to be timezone-aware.

    Discovery strategies disagree: RSS/Google News always produce UTC-aware
    timestamps, but a sitemap's <lastmod> may have no offset at all. Sorting a
    response that mixes both raises "can't compare offset-naive and
    offset-aware datetimes", so every article's timestamp is coerced here —
    the single point all of them pass through.
    """
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    return _as_utc(parsed)


def _normalize_article(item: Dict, source: Source) -> Optional[Dict]:
    url = item['url']
    page = fetch_article_page(url)

    # Category/tag/archive listings look like articles by URL shape but have no
    # story behind them — drop them rather than serving empty-bodied results.
    if page.get('is_listing'):
        return None

    # Nor is anything else we couldn't read a body from worth returning: a card
    # with a headline and no text is noise to SocEye. This covers pages that
    # render their body in JavaScript (CGTN's video transcripts) and fetches
    # that were blocked or timed out.
    if not (page.get('content') or '').strip():
        return None

    title = (item.get('title') or page.get('title') or '').strip()
    summary = (item.get('summary') or page.get('description') or '').strip()
    content = page.get('content') or ''
    # No stand-in image. The old fallback was an Indian flag, which is simply
    # wrong on a story from Tokyo or Lagos; an empty value lets the client
    # lay the card out without one.
    # Normalized here rather than in either producer, so the feed's image and
    # the scraped page's image both reach the client over https — see
    # prefer_https() for why an http:// image is useless to a TLS-served client.
    image_url = prefer_https(item.get('image_url') or page.get('image') or '')
    # Prefer the feed's date, then the page's own published_time. Falling
    # straight through to now() would stamp every article with today.
    published_at = (
        _as_utc(item.get('published_at'))
        or _parse_iso(page.get('published'))
        or datetime.now(timezone.utc)
    )

    text_for_location = f"{title} {summary} {content}"
    location = resolve_location(source.state, text_for_location)

    return {
        'id': make_article_id(url),
        'title': title,
        'content': content,
        'summary': summary,
        'source': source.name,
        'source_id': source.id,
        'source_url': url,
        'language': source.language,
        # Country comes straight from the registry — it is curated per source,
        # never guessed from the article text.
        'country': source.country,
        'state': location['state'],
        'district': location['district'],
        'location': location['location'],
        'published_at': published_at,
        'image_url': image_url,
    }


def _scrape_source(source: Source) -> List[Dict]:
    raw_items = discovery.discover_source(source)
    articles = []
    for item in raw_items:
        if not item.get('url'):
            continue
        try:
            article = _normalize_article(item, source)
        except Exception:
            continue
        if article and article['title']:
            articles.append(article)
    return dedupe_by_id(articles)


def _select_sources(
    country: FilterValue = None,
    state: FilterValue = None,
    language: FilterValue = None,
    source: FilterValue = None,
) -> List[Source]:
    """Narrow the registry to the sources a request could possibly match
    BEFORE any scraping happens — that is what keeps `country=Japan` from
    fetching 300+ irrelevant sources. An unfiltered request still falls back
    to the _MAX_SOURCES_PER_REQUEST safety limit."""
    candidates = list_active_sources(
        country=country, state=state, language=language, source_id=source,
    )
    if len(candidates) > _MAX_SOURCES_PER_REQUEST:
        candidates = candidates[:_MAX_SOURCES_PER_REQUEST]
    return candidates


def _fetch_sources(sources: List[Source]) -> List[Dict]:
    """Fetch (or reuse cached) articles for each source concurrently. A single
    source raising never fails the request — it just contributes nothing."""
    all_articles: List[Dict] = []
    if not sources:
        return all_articles

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {
            pool.submit(cache.get_or_fetch, s.id, lambda s=s: _scrape_source(s)): s
            for s in sources
        }
        for future in as_completed(futures):
            try:
                all_articles.extend(future.result())
            except Exception:
                continue
    return all_articles


def _any_match(haystack: str, needles: Sequence[str]) -> bool:
    """OR within one filter category; an empty selection never restricts."""
    if not needles:
        return True
    lowered = (haystack or '').lower()
    return any(n.lower() in lowered for n in needles)


def get_articles(
    keyword: Optional[str] = None,
    country: FilterValue = None,
    language: FilterValue = None,
    location: FilterValue = None,
    state: FilterValue = None,
    district: FilterValue = None,
    source: FilterValue = None,
    limit: int = 20,
    offset: int = 0,
) -> Dict:
    """Filters combine as (a OR b) AND (c OR d): values selected within one
    checkbox group are alternatives, separate groups all have to hold. An
    omitted filter never restricts results."""
    countries = parse_multi(country)
    languages = parse_multi(language)
    locations = parse_multi(location)
    states = parse_multi(state)
    districts = parse_multi(district)
    sources_wanted = parse_multi(source)

    selected = _select_sources(
        country=countries, state=states, language=languages, source=sources_wanted,
    )
    articles = _fetch_sources(selected)
    articles = dedupe_by_id(articles)

    def _match(a: Dict) -> bool:
        if not matches_keyword(a['title'], a['summary'], a['content'], keyword):
            return False
        if not _any_match(a.get('country', ''), countries):
            return False
        if not _any_match(a['language'], languages):
            return False
        if not _any_match(a['state'], states):
            return False
        if not _any_match(a['district'], districts):
            return False
        # `location` is the loose one — it matches against whatever place
        # information the article actually carries, so a city name can hit
        # either the detected district or the source's state.
        if not _any_match(f"{a['location']} {a['district']} {a['state']}", locations):
            return False
        return True

    filtered = [a for a in articles if _match(a)]
    # Defensive: normalizing at write time above is the real fix, but a
    # single stray naive timestamp must never turn a whole request into a 500.
    filtered.sort(key=lambda a: _as_utc(a['published_at']) or _EPOCH, reverse=True)

    total = len(filtered)
    page = filtered[offset: offset + limit]
    return {'count': total, 'limit': limit, 'offset': offset, 'articles': page}


def get_article_by_id(article_id: str) -> Optional[Dict]:
    """Only finds articles currently in the warm in-memory cache — there is
    no persistent store to fall back to. Callers get a 404 for anything
    that has aged out of the cache or was never scraped in this process."""
    for article in cache.all_cached_articles():
        if article['id'] == article_id:
            return article
    return None


def refresh_all_sources() -> int:
    """Force-refresh every active source's cache. Used by the optional
    background_worker.py pre-warmer — not required for the API to function."""
    sources = list_all_sources()
    sources = [s for s in sources if s.active]
    total = 0
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(_scrape_source, s): s for s in sources}
        for future in as_completed(futures):
            s = futures[future]
            try:
                articles = future.result()
                cache.set(s.id, articles)
                total += len(articles)
            except Exception:
                continue
    return total
