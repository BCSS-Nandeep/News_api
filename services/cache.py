"""
Process-wide, in-memory TTL cache of scraped articles, keyed by source id.

Nothing is persisted, so a restart starts cold and articles are re-discovered
lazily on the next request that needs that source. This is what stops every
request from re-scraping sources it already fetched moments ago.
"""

import os
import threading
import time
from typing import Callable, Dict, List, Optional

_TTL_SECONDS = int(os.getenv('CACHE_TTL_SECONDS', '600'))

_lock = threading.Lock()
_store: Dict[str, Dict] = {}  # source_id -> {'articles': [...], 'fetched_at': float}


def is_fresh(source_id: str) -> bool:
    with _lock:
        entry = _store.get(source_id)
    return bool(entry and (time.time() - entry['fetched_at']) < _TTL_SECONDS)


def get(source_id: str) -> Optional[List[dict]]:
    with _lock:
        entry = _store.get(source_id)
    return list(entry['articles']) if entry else None


def set(source_id: str, articles: List[dict]) -> None:
    with _lock:
        _store[source_id] = {'articles': articles, 'fetched_at': time.time()}


def get_or_fetch(source_id: str, fetch_fn: Callable[[], List[dict]]) -> List[dict]:
    """Return cached articles if fresh, otherwise call fetch_fn() and cache the result."""
    if is_fresh(source_id):
        return get(source_id)
    articles = fetch_fn()
    set(source_id, articles)
    return articles


def all_cached_articles() -> List[dict]:
    """Every article currently held in the cache, across all sources — used
    for GET /news/articles/{article_id}, which can only find what's warm."""
    with _lock:
        snapshot = list(_store.values())
    out = []
    for entry in snapshot:
        out.extend(entry['articles'])
    return out


def clear() -> None:
    """Mainly for tests."""
    with _lock:
        _store.clear()
