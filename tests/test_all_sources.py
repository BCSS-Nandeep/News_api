"""
Every eligible source is searched, each source is scraped once at a time, and
the background refresh keeps all active sources cached. No network — scraping
is replaced by fakes (thread mode, see conftest.py).
"""

import logging
import threading
import time
from datetime import datetime, timezone

import pytest

from scraper import discovery
from scraper.sources_registry import list_active_sources, reload_registry
from services import cache, news_service, scrape_pool

PUBLISHED = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def article(source, article_id=None, title=None):
    return {
        'id': article_id or f'art_{source.id}', 'title': title or f'{source.name} headline',
        'content': 'Body.', 'summary': 'Summary.', 'source': source.name, 'source_id': source.id,
        'source_url': f'{source.base_url}a', 'language': source.language, 'country': source.country,
        'state': source.state, 'district': '', 'location': '', 'published_at': PUBLISHED, 'image_url': '',
    }


@pytest.fixture
def fresh_pool(monkeypatch):
    """A pool of its own (fast watchdog, short source timeout) so a test can shut
    it down or time sources out without touching the shared one."""
    monkeypatch.setattr(news_service, '_pool', None)
    monkeypatch.setattr(news_service, '_SOURCE_TIMEOUT_SECONDS', 0.5)
    monkeypatch.setattr(scrape_pool, '_WATCHDOG_SECONDS', 0.05)
    yield
    if news_service._pool is not None:
        news_service._pool.shutdown()


@pytest.fixture
def counted_scraper(monkeypatch):
    calls, lock = [], threading.Lock()
    behaviour = {}  # source id -> 'fail' | 'hang' | ('sleep', s) | list of articles

    def fake(source):
        with lock:
            calls.append(source.id)
        b = behaviour.get(source.id)
        if b == 'fail':
            raise RuntimeError('site down')
        if b == 'hang':
            time.sleep(2)
        if isinstance(b, tuple):
            time.sleep(b[1])
        if isinstance(b, list):
            return b
        return [article(source)]

    monkeypatch.setattr(news_service, '_scrape_source', fake)
    return calls, behaviour


class TestRealRegistry:
    """The actual News_URLs.json — no hidden cap anywhere in source selection."""

    def setup_method(self):
        reload_registry()

    def test_no_filters_selects_every_active_source(self):
        selected = news_service._select_sources()
        assert len(selected) == len(list_active_sources()) > 300

    def test_state_filter_selects_every_matching_source(self):
        selected = news_service._select_sources(state='Odisha')
        assert len(selected) >= 10
        assert all('odisha' in s.state.lower() for s in selected)

    def test_country_filter_is_not_truncated(self):
        india = [s for s in list_active_sources() if s.country == 'India']
        assert len(news_service._select_sources(country='India')) == len(india) > 40


class TestRequests:
    def test_response_reports_sources_searched_and_failed(self, fixture_registry, counted_scraper):
        _, behaviour = counted_scraper
        behaviour['test_tamil_nadu'] = 'fail'
        result = news_service.get_articles()
        assert result['sources_searched'] == 6
        assert result['sources_failed'] == 1
        assert result['pending_sources'] == 0
        assert result['count'] == 5

    def test_concurrent_requests_scrape_each_source_once(self, fixture_registry, counted_scraper):
        calls, behaviour = counted_scraper
        for s in list_active_sources():
            behaviour[s.id] = ('sleep', 0.3)
        results = []
        threads = [threading.Thread(target=lambda: results.append(news_service.get_articles(limit=50)))
                   for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(calls) == sorted(s.id for s in list_active_sources())
        assert all(r['count'] == 6 for r in results)

    def test_the_same_article_from_two_sources_is_returned_once(self, fixture_registry, counted_scraper):
        _, behaviour = counted_scraper
        us, uk = (next(s for s in list_active_sources() if s.id == sid)
                  for sid in ('test_us_english', 'test_uk_english'))
        behaviour[us.id] = [article(us, 'shared-story', 'Shared wire story')]
        behaviour[uk.id] = [article(uk, 'shared-story', 'Shared wire story')]
        result = news_service.get_articles(keyword='shared wire story')
        assert [a['id'] for a in result['articles']] == ['shared-story']

    def test_request_is_logged_with_source_coverage(self, fixture_registry, counted_scraper, caplog):
        with caplog.at_level(logging.INFO, logger='blura.news'):
            news_service.get_articles(country='India')
        line = next(r.getMessage() for r in caplog.records if 'articles request' in r.getMessage())
        assert '3 eligible of 6 active sources' in line


class TestBackgroundRefresh:
    def test_round_attempts_every_active_source(self, fixture_registry, counted_scraper, fresh_pool):
        calls, behaviour = counted_scraper
        behaviour['test_tamil_nadu'] = 'fail'
        behaviour['test_global_agency'] = 'hang'
        stats = news_service._refresh_round()
        assert stats['active'] == stats['attempted'] == 6
        assert (stats['ok'], stats['failed'], stats['timed_out']) == (4, 1, 1)
        assert stats['articles'] == 4
        assert sorted(calls) == sorted(s.id for s in list_active_sources())

    def test_next_round_only_retries_sources_that_are_not_fresh(
            self, fixture_registry, counted_scraper, fresh_pool, monkeypatch):
        monkeypatch.setattr(news_service, '_REFRESH_SECONDS', 600)
        calls, behaviour = counted_scraper
        behaviour['test_tamil_nadu'] = 'fail'
        news_service._refresh_round()
        calls.clear()
        stats = news_service._refresh_round()
        assert calls == ['test_tamil_nadu']
        assert stats['attempted'] == 1

    def test_refresh_all_sources_returns_the_article_total(self, fixture_registry, counted_scraper, fresh_pool):
        assert news_service.refresh_all_sources() == 6

    def test_background_thread_fills_the_cache(self, fixture_registry, counted_scraper, fresh_pool, monkeypatch):
        monkeypatch.setattr(news_service, '_REFRESH_SECONDS', 600)
        assert news_service.start_background_refresh()
        deadline = time.time() + 5
        while time.time() < deadline and not all(cache.is_fresh(s.id) for s in list_active_sources()):
            time.sleep(0.05)
        news_service.stop_background_refresh()
        assert all(cache.is_fresh(s.id) for s in list_active_sources())

    def test_disabled_when_interval_is_zero(self, monkeypatch):
        monkeypatch.setattr(news_service, '_REFRESH_SECONDS', 0)
        assert news_service.start_background_refresh() is False


def test_new_defaults():
    assert discovery._MAX_ITEMS_PER_SOURCE == 50
    assert cache._TTL_SECONDS == 1800
