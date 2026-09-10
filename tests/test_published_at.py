"""
Timezone handling for article timestamps.

Regression cover for a live 500: `GET /news/articles?country=Pakistan` raised
"can't compare offset-naive and offset-aware datetimes" from the sort in
get_articles(). Discovery strategies disagree about tzinfo — RSS and Google
News always emit UTC-aware datetimes, but a sitemap's <lastmod> is frequently
a bare date with no offset. A request whose sources resolve through BOTH paths
then mixes the two in one list and the sort raises.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from api.main import app
from scraper import discovery
from services import news_service

client = TestClient(app, raise_server_exceptions=False)


class TestSitemapTimestampsAreAware:
    """discovery._resolve_sitemap must never emit a naive datetime."""

    def _sitemap(self, lastmod):
        return ET.fromstring(
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            '<url><loc>https://example.test/news/story-about-something/</loc>'
            f'<lastmod>{lastmod}</lastmod></url>'
            '</urlset>'
        )

    @pytest.mark.parametrize('lastmod', [
        '2026-09-10',                  # bare date — the common Pakistan case
        '2026-09-10T14:30:00',         # datetime, no offset
        '2026-09-10T14:30:00Z',        # already UTC
        '2026-09-10T14:30:00+05:00',   # already offset-aware
    ])
    def test_lastmod_always_yields_an_aware_datetime(self, lastmod):
        items = discovery._resolve_sitemap(self._sitemap(lastmod))
        assert len(items) == 1
        published = items[0]['published_at']
        assert published is not None
        assert published.tzinfo is not None, f'{lastmod} produced a naive datetime'

    def test_unparseable_lastmod_is_left_as_none(self):
        items = discovery._resolve_sitemap(self._sitemap('not-a-date'))
        assert items[0]['published_at'] is None


class TestAsUtc:
    def test_naive_is_assumed_utc(self):
        naive = datetime(2026, 9, 10, 14, 30)
        assert news_service._as_utc(naive) == datetime(2026, 9, 10, 14, 30, tzinfo=timezone.utc)

    def test_aware_is_left_alone(self):
        aware = datetime(2026, 9, 10, 14, 30, tzinfo=timezone.utc)
        assert news_service._as_utc(aware) is aware

    def test_none_stays_none(self):
        assert news_service._as_utc(None) is None


def _article(article_id, published_at, source_id='s'):
    return {
        'id': article_id, 'title': f'Title {article_id}', 'content': 'body',
        'summary': 'summary', 'source': 'Src', 'source_id': source_id,
        'source_url': f'https://example.test/{article_id}', 'language': 'English',
        'country': 'Pakistan', 'state': '', 'district': '', 'location': '',
        'published_at': published_at, 'image_url': '',
    }


class TestMixedTimestampsDoNotCrash:
    """The exact live failure: one source resolves via sitemap (naive), another
    via RSS (aware), and both land in the same response."""

    NAIVE = datetime(2026, 9, 9, 8, 0)
    AWARE = datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)

    def _install(self, monkeypatch, fixture_registry):
        def fake_scrape(source):
            if source.id == 'test_us_english':
                return [_article('naive', self.NAIVE, source.id)]
            if source.id == 'test_uk_english':
                return [_article('aware', self.AWARE, source.id)]
            return []

        monkeypatch.setattr(news_service, '_scrape_source', fake_scrape)

    def test_get_articles_sorts_mixed_timestamps(self, fixture_registry, monkeypatch):
        self._install(monkeypatch, fixture_registry)
        result = news_service.get_articles(source='test_us_english,test_uk_english')
        assert result['count'] == 2
        # Newest first — the aware one is a day later than the naive one.
        assert [a['id'] for a in result['articles']] == ['aware', 'naive']

    def test_endpoint_returns_200_not_500(self, fixture_registry, monkeypatch):
        self._install(monkeypatch, fixture_registry)
        resp = client.get('/news/articles', params={'source': 'test_us_english,test_uk_english'})
        assert resp.status_code == 200
        assert resp.json()['count'] == 2

    def test_normalized_articles_are_always_aware(self, fixture_registry, monkeypatch):
        """A naive timestamp from discovery is coerced before it reaches the dict."""
        monkeypatch.setattr(
            news_service, 'fetch_article_page',
            lambda url: {'title': 'T', 'description': 'D', 'content': 'C', 'image': '', 'published': None},
        )
        source = fixture_registry.get_source('test_us_english')
        article = news_service._normalize_article(
            {'url': 'https://example.test/a', 'title': 'T', 'published_at': self.NAIVE}, source,
        )
        assert article['published_at'].tzinfo is not None

    def test_sort_survives_a_stray_naive_timestamp_in_cache(self, fixture_registry, monkeypatch):
        """Belt and braces: even if a naive value somehow reaches the article
        list (e.g. a cache entry written before this fix), the request must not
        become a 500."""
        def fake_scrape(source):
            if source.id == 'test_us_english':
                # Bypasses _normalize_article entirely, like a stale cache entry.
                return [_article('stale', self.NAIVE, source.id)]
            if source.id == 'test_uk_english':
                return [_article('fresh', self.AWARE, source.id)]
            return []

        monkeypatch.setattr(news_service, '_scrape_source', fake_scrape)
        result = news_service.get_articles(source='test_us_english,test_uk_english')
        assert result['count'] == 2
