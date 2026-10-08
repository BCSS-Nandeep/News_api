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

import logging
import os
import threading
import time
from concurrent.futures import Future, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from scraper import discovery
from scraper.extraction import fetch_article_page, prefer_https
from scraper.normalize import make_article_id, dedupe_by_id
from scraper.sources_registry import (
    FilterValue,
    Source,
    list_active_sources,
    parse_multi,
)
from processing.location import resolve_location
from processing.keywords import KeywordMatch, SearchText, match_search_text, parse_keyword, search_text
from services import cache
from services.scrape_pool import JobTimeout, ScrapePool

log = logging.getLogger('blura.news')

# Scraping parallelism. Parsing pages is CPU work and one Python process uses
# one core, so sources are scraped in SCRAPE_PROCESSES worker processes, each
# running SCRAPE_THREADS_PER_PROCESS sources at a time. 0 processes = threads in
# this process (tests, local runs). Workers run at SCRAPE_NICE so the server's
# other services get the CPU first.
_PROCESSES = int(os.getenv('SCRAPE_PROCESSES', '4'))
_THREADS_PER_PROCESS = int(os.getenv('SCRAPE_THREADS_PER_PROCESS', '6'))
_SCRAPE_NICE = int(os.getenv('SCRAPE_NICE', '10'))
# Hard stop for one source's scrape; it then counts as failed and is retried later.
_SOURCE_TIMEOUT_SECONDS = float(os.getenv('SOURCE_TIMEOUT_SECONDS', '600'))
# Every active source is re-scraped in the background this often (0 = off), so
# requests are answered from cache instead of waiting for scrapes.
_REFRESH_SECONDS = float(os.getenv('BACKGROUND_REFRESH_SECONDS', '600'))
# A cold source takes ~15 s to scrape — but gateways in front of this API
# (BluGate) cut requests off at 30 s. Answering within this budget with the
# sources that are ready beats a timeout with none.
_TIME_BUDGET_SECONDS = float(os.getenv('REQUEST_TIME_BUDGET_SECONDS', '25'))

# Requests overtake background refreshes in the scrape queue.
_PRIORITY_REQUEST = 0
_PRIORITY_REFRESH = 1

_pool: Optional[ScrapePool] = None
_pool_lock = threading.Lock()

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


# Private key on cached articles: their keyword-matching text, prepared once when
# the source is scraped. Normalizing ~5,000 article bodies on every request took
# seconds once all sources were searched, pushing responses past the gateway's
# 30 s cutoff. Never part of a response (see _public).
_SEARCH_KEY = '_search'


def _scrape_prepared(source: Source) -> List[Dict]:
    """_scrape_source plus each article's search text, built in the scraping
    thread before the articles are cached (nothing else can see them yet)."""
    articles = _scrape_source(source)
    for a in articles:
        a[_SEARCH_KEY] = search_text(a['title'], a['summary'], a['content'])
    return articles


def _searchable(article: Dict) -> SearchText:
    # Built here only for an article cached without it; cached dicts are shared
    # between requests, so it is not stored back.
    return article.get(_SEARCH_KEY) or search_text(
        article['title'], article['summary'], article['content'],
    )


def _public(article: Dict) -> Dict:
    return {k: v for k, v in article.items() if k != _SEARCH_KEY}


def _select_sources(
    country: FilterValue = None,
    state: FilterValue = None,
    language: FilterValue = None,
    source: FilterValue = None,
) -> List[Source]:
    """Narrow the registry to the sources a request could possibly match
    BEFORE any scraping happens — that is what keeps `country=Japan` from
    fetching 300+ irrelevant sources. No filter means every active source,
    and a filtered list is never truncated: a cap here would silently drop
    whole regions (the first 40 registry entries are all South Indian). The
    scrape pool's fixed size, per-source limits in discovery and the request
    time budget are what keep fetching all of them bounded."""
    return list_active_sources(
        country=country, state=state, language=language, source_id=source,
    )


def _get_pool() -> ScrapePool:
    """Created on first use. Results are cached in this process by on_done, so
    worker processes never touch shared state."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ScrapePool(
                _scrape_prepared,
                processes=_PROCESSES,
                threads_per_process=_THREADS_PER_PROCESS,
                job_timeout=_SOURCE_TIMEOUT_SECONDS,
                on_done=cache.set,
                nice=_SCRAPE_NICE,
            )
        return _pool


def _scrape_in_background(source: Source, priority: int = _PRIORITY_REQUEST) -> Future:
    """At most one scrape per source at a time: a request arriving while that
    source is already queued or scraping (for a request or the background
    refresh) waits on that scrape rather than starting a second one."""
    return _get_pool().submit(source.id, source, priority)


@dataclass
class _FetchStats:
    eligible: int
    cached: int = 0      # served from fresh cache
    scraped: int = 0     # not fresh, so scraped (or joined a running scrape)
    failed: int = 0      # scrape raised or timed out
    pending: int = 0     # still scraping when the time budget ran out
    articles: int = 0
    seconds: float = 0.0


def _fetch_sources(sources: List[Source]) -> Tuple[List[Dict], _FetchStats]:
    """Articles for each source — cached ones immediately, the rest scraped
    concurrently for up to _TIME_BUDGET_SECONDS. Sources still scraping when
    the budget runs out finish in the background and are served from cache
    next time (stats.pending). A single source raising never fails the
    request — it just contributes nothing (stats.failed)."""
    started = time.monotonic()
    stats = _FetchStats(eligible=len(sources))
    all_articles: List[Dict] = []
    futures: List[Future] = []
    for s in sources:
        # Fresh cache is read here, not via the pool, so a cache hit never
        # queues behind other requests' slow scrapes.
        cached = cache.get(s.id) if cache.is_fresh(s.id) else None
        if cached is not None:
            stats.cached += 1
            all_articles.extend(cached)
        else:
            futures.append(_scrape_in_background(s))

    stats.scraped = len(futures)
    if futures:
        done, pending = wait(futures, timeout=_TIME_BUDGET_SECONDS)
        stats.pending = len(pending)
        for future in done:
            try:
                all_articles.extend(future.result())
            except Exception:
                stats.failed += 1
    stats.articles = len(all_articles)
    stats.seconds = time.monotonic() - started
    return all_articles, stats


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
    min_match: int = 1,
) -> Dict:
    """Filters combine as (a OR b) AND (c OR d): values selected within one
    checkbox group are alternatives, separate groups all have to hold. An
    omitted filter never restricts results. `keyword` is one keyword (matched as a
    whole phrase) or several separated by commas — see processing/keywords.py."""
    countries = parse_multi(country)
    languages = parse_multi(language)
    locations = parse_multi(location)
    states = parse_multi(state)
    districts = parse_multi(district)
    sources_wanted = parse_multi(source)

    selected = _select_sources(
        country=countries, state=states, language=languages, source=sources_wanted,
    )
    articles, stats = _fetch_sources(selected)
    articles = dedupe_by_id(articles)
    query = parse_keyword(keyword)

    def _match(a: Dict) -> bool:
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

    ranked = []
    for a in articles:
        if not _match(a):
            continue
        match = (
            match_search_text(_searchable(a), query, min_match)
            if query.phrases else KeywordMatch([], [], 0)
        )
        if match is None:
            continue
        # Copy: `a` is the cached dict shared with every other request.
        ranked.append(({
            **_public(a),
            'matched_terms': match.matched_terms,
            'matched_phrases': match.matched_phrases,
            'match_score': match.score,
        }, match.sort_key))

    # Best keyword match first, then newest, then id so pages never reshuffle.
    # Defensive _as_utc: normalizing at write time is the real fix, but a single
    # stray naive timestamp must never turn a whole request into a 500.
    ranked.sort(key=lambda pair: (
        pair[1],
        _as_utc(pair[0]['published_at']) or _EPOCH,
        pair[0]['id'],
    ), reverse=True)
    filtered = [a for a, _ in ranked]

    total = len(filtered)
    page = filtered[offset: offset + limit]
    log.info(
        'articles request: %d eligible of %d active sources | %d from cache, %d scraped '
        '(%d failed, %d still running) | %d articles, %d matched | %.1fs',
        stats.eligible, len(list_active_sources()), stats.cached, stats.scraped,
        stats.failed, stats.pending, len(articles), total, stats.seconds,
    )
    return {
        'count': total,
        'limit': limit,
        'offset': offset,
        'articles': page,
        'pending_sources': stats.pending,
        'sources_searched': stats.eligible,
        'sources_failed': stats.failed,
        'query_terms': query.terms,
        'query_phrases': [p.text for p in query.phrases],
    }


def get_article_by_id(article_id: str) -> Optional[Dict]:
    """Only finds articles currently in the warm in-memory cache — there is
    no persistent store to fall back to. Callers get a 404 for anything
    that has aged out of the cache or was never scraped in this process."""
    for article in cache.all_cached_articles():
        if article['id'] == article_id:
            return _public(article)
    return None


def _refresh(sources: List[Source], label: str, active: int) -> Dict:
    """Scrape `sources` through the pool (behind any user requests), wait for
    every one to finish, fail or time out, and log the round."""
    started = time.monotonic()
    futures = {s.id: _scrape_in_background(s, _PRIORITY_REFRESH) for s in sources}
    wait(futures.values())
    stats = {'active': active, 'attempted': len(futures), 'ok': 0, 'empty': 0,
             'failed': 0, 'timed_out': 0, 'articles': 0}
    for future in futures.values():
        exc = future.exception()
        if exc is None:
            stats['ok'] += 1
            stats['articles'] += len(future.result())
            stats['empty'] += not future.result()
        elif isinstance(exc, JobTimeout):
            stats['timed_out'] += 1
        else:
            stats['failed'] += 1
    stats['seconds'] = round(time.monotonic() - started, 1)
    log.info(
        '%s: %d active sources, %d attempted | %d ok (%d with no articles), %d failed, '
        '%d timed out | %d articles | %.0fs',
        label, active, stats['attempted'], stats['ok'], stats['empty'], stats['failed'],
        stats['timed_out'], stats['articles'], stats['seconds'],
    )
    return stats


def refresh_all_sources() -> int:
    """Force-refresh every active source's cache; returns the article count.
    Used by background_worker.py --once."""
    sources = list_active_sources()
    return _refresh(sources, 'full refresh', len(sources))['articles']


# ── background refresh ─────────────────────────────────────────────────────────

_refresh_stop = threading.Event()
_refresh_thread: Optional[threading.Thread] = None


def _refresh_round() -> Dict:
    """One background round: every active source not refreshed within the last
    interval (never scraped, failed last time, or getting old). Sources a
    request scraped recently are skipped."""
    sources = list_active_sources()
    due_after = max(0.0, _REFRESH_SECONDS - 60)  # slack: a round's sources finish minutes apart
    due = [s for s in sources if (cache.age(s.id) is None or cache.age(s.id) >= due_after)]
    return _refresh(due, 'background refresh', len(sources))


def _refresh_loop() -> None:
    while not _refresh_stop.is_set():
        started = time.monotonic()
        try:
            _refresh_round()
        except Exception:
            log.exception('background refresh round failed')
        _refresh_stop.wait(max(5.0, _REFRESH_SECONDS - (time.monotonic() - started)))


def start_background_refresh() -> bool:
    """Keep every active source's articles cached so requests rarely wait.
    Called on API startup; off when BACKGROUND_REFRESH_SECONDS=0."""
    global _refresh_thread
    if _REFRESH_SECONDS <= 0 or (_refresh_thread and _refresh_thread.is_alive()):
        return False
    _refresh_stop.clear()
    _refresh_thread = threading.Thread(target=_refresh_loop, name='news-refresh', daemon=True)
    _refresh_thread.start()
    log.info('background refresh every %.0f s; scraping with %s', _REFRESH_SECONDS, _get_pool().mode)
    return True


def stop_background_refresh() -> None:
    _refresh_stop.set()
    with _pool_lock:
        pool = _pool
    if pool is not None:
        pool.shutdown()
