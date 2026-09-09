"""
Orchestrates the whole DISCOVER -> SCRAPE -> NORMALIZE -> FILTER -> PAGINATE
flow for the News API. This is the only place that ties scraper/, processing/
and services/cache together — routes call this, never the lower layers
directly (see api/routes/articles.py).
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict, List, Optional

from scraper import discovery
from scraper.extraction import fetch_article_page, DEFAULT_IMAGE_URL
from scraper.normalize import make_article_id, dedupe_by_id
from scraper.sources_registry import Source, list_active_sources, list_all_sources
from processing.location import resolve_location
from processing.keywords import matches_keyword
from services import cache

_MAX_SOURCES_PER_REQUEST = int(os.getenv('MAX_SOURCES_PER_REQUEST', '40'))
_MAX_WORKERS = int(os.getenv('DISCOVERY_MAX_WORKERS', '10'))


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _normalize_article(item: Dict, source: Source) -> Optional[Dict]:
    url = item['url']
    page = fetch_article_page(url)

    # Category/tag/archive listings look like articles by URL shape but have no
    # story behind them — drop them rather than serving empty-bodied results.
    if page.get('is_listing'):
        return None

    title = (item.get('title') or page.get('title') or '').strip()
    summary = (item.get('summary') or page.get('description') or '').strip()
    content = page.get('content') or ''
    image_url = item.get('image_url') or page.get('image') or DEFAULT_IMAGE_URL
    # Prefer the feed's date, then the page's own published_time. Falling
    # straight through to now() would stamp every article with today.
    published_at = (
        item.get('published_at')
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


def _select_sources(state: Optional[str], language: Optional[str], source: Optional[str]) -> List[Source]:
    candidates = list_active_sources(state=state, language=language, source_id=source)
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


def get_articles(
    keyword: Optional[str] = None,
    language: Optional[str] = None,
    location: Optional[str] = None,
    state: Optional[str] = None,
    district: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> Dict:
    """All filters are ANDed: an omitted filter never restricts results."""
    sources = _select_sources(state=state, language=language, source=source)
    articles = _fetch_sources(sources)
    articles = dedupe_by_id(articles)

    def _match(a: Dict) -> bool:
        if not matches_keyword(a['title'], a['summary'], a['content'], keyword):
            return False
        if language and language.strip().lower() not in (a['language'] or '').lower():
            return False
        if state and state.strip().lower() not in (a['state'] or '').lower():
            return False
        if district and district.strip().lower() not in (a['district'] or '').lower():
            return False
        if location and location.strip().lower() not in (
            f"{a['location']} {a['district']} {a['state']}".lower()
        ):
            return False
        return True

    filtered = [a for a in articles if _match(a)]
    filtered.sort(key=lambda a: a['published_at'], reverse=True)

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
