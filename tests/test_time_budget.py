"""
Request time budget: a request answers within REQUEST_TIME_BUDGET_SECONDS with
the sources that are ready; slower sources keep scraping in the background and
are served from cache next time. No network — scraping is a controllable fake.
"""

import threading
import time
from datetime import datetime, timezone

import pytest

from services import cache, news_service

PUBLISHED = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
FAST = 'test_telangana_telugu'
SLOW = 'test_tamil_nadu'
BOTH = f'{FAST},{SLOW}'


def article_for(source):
    return {
        'id': f'art_{source.id}', 'title': f'{source.name} headline', 'content': 'Body.',
        'summary': 'Summary.', 'source': source.name, 'source_id': source.id,
        'source_url': f'{source.base_url}a', 'language': source.language,
        'country': source.country, 'state': source.state, 'district': '', 'location': '',
        'published_at': PUBLISHED, 'image_url': '',
    }


@pytest.fixture
def slow_scraper(monkeypatch, fixture_registry):
    """FAST scrapes instantly; SLOW blocks until `release` is set."""
    release = threading.Event()
    calls = []

    def fake_scrape(source):
        calls.append(source.id)
        if source.id == SLOW:
            release.wait(timeout=10)
        return [article_for(source)]

    monkeypatch.setattr(news_service, '_scrape_source', fake_scrape)
    monkeypatch.setattr(news_service, '_TIME_BUDGET_SECONDS', 0.3)
    yield release, calls
    release.set()  # never leave a pool thread blocked for the next test


def wait_until(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_answers_within_budget_with_ready_sources(slow_scraper):
    release, _ = slow_scraper
    started = time.time()
    result = news_service.get_articles(source=BOTH)
    elapsed = time.time() - started

    assert elapsed < 2, 'must not wait for the slow source'
    assert {a['source_id'] for a in result['articles']} == {FAST}
    assert result['count'] == 1
    assert result['pending_sources'] == 1


def test_slow_source_finishes_in_background_and_is_served_next_time(slow_scraper):
    release, calls = slow_scraper
    news_service.get_articles(source=BOTH)

    release.set()
    assert wait_until(lambda: cache.is_fresh(SLOW)), 'background scrape should populate the cache'

    result = news_service.get_articles(source=BOTH)
    assert {a['source_id'] for a in result['articles']} == {FAST, SLOW}
    assert result['pending_sources'] == 0
    assert calls.count(SLOW) == 1, 'the second request uses the cache, no re-scrape'


def test_retry_while_still_scraping_does_not_start_a_second_scrape(slow_scraper):
    release, calls = slow_scraper
    first = news_service.get_articles(source=BOTH)
    second = news_service.get_articles(source=BOTH)

    assert first['pending_sources'] == second['pending_sources'] == 1
    assert calls.count(SLOW) == 1


def test_cached_sources_never_wait(monkeypatch, fixture_registry):
    def must_not_scrape(source):
        raise AssertionError('cached source was scraped again')

    for sid in (FAST, SLOW):
        src = next(s for s in fixture_registry.list_all_sources() if s.id == sid)
        cache.set(sid, [article_for(src)])
    monkeypatch.setattr(news_service, '_scrape_source', must_not_scrape)
    monkeypatch.setattr(news_service, '_TIME_BUDGET_SECONDS', 0)

    result = news_service.get_articles(source=BOTH)
    assert result['count'] == 2
    assert result['pending_sources'] == 0


def test_failing_source_is_not_pending(monkeypatch, fixture_registry):
    def flaky(source):
        if source.id == SLOW:
            raise RuntimeError('site down')
        return [article_for(source)]

    monkeypatch.setattr(news_service, '_scrape_source', flaky)
    result = news_service.get_articles(source=BOTH)
    assert {a['source_id'] for a in result['articles']} == {FAST}
    assert result['pending_sources'] == 0
