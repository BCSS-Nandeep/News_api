"""
No live network calls here — every HTTP boundary is mocked. Verifies (a) the
fallback chain tries strategies in order and stops at the first success, and
(b) rss_discover can actually parse a real RSS feed given a mocked response.
"""

from types import SimpleNamespace

import pytest

from scraper import discovery
from scraper.sources_registry import Source

SOURCE = Source(
    id="test_source", name="Test Source", region="South India", state="Telangana",
    language="Telugu", type="tv_news", base_url="https://example.test/", active=True,
)


def _fake_response(text="", content=b"", content_type="application/xml"):
    return SimpleNamespace(
        status_code=200, text=text, content=content or text.encode("utf-8"),
        headers={"Content-Type": content_type},
    )


class TestDiscoverSourceChain:
    def test_stops_at_first_successful_strategy(self, monkeypatch):
        calls = []

        def rss_ok(source):
            calls.append("rss")
            return [{"url": "https://example.test/article-one", "title": "A", "summary": "", "image_url": None, "published_at": None}]

        def should_not_run(source):
            calls.append("should_not_run")
            raise AssertionError("this strategy should never be called")

        monkeypatch.setattr(discovery, "_STRATEGIES", (rss_ok, should_not_run, should_not_run, should_not_run))

        result = discovery.discover_source(SOURCE)
        assert calls == ["rss"]
        assert len(result) == 1

    def test_falls_through_to_later_strategy_when_earlier_ones_find_nothing(self, monkeypatch):
        calls = []

        def empty(source):
            calls.append("empty")
            return []

        def finds_something(source):
            calls.append("found")
            return [{"url": "https://example.test/x", "title": "X", "summary": "", "image_url": None, "published_at": None}]

        monkeypatch.setattr(discovery, "_STRATEGIES", (empty, empty, finds_something, empty))

        result = discovery.discover_source(SOURCE)
        assert calls == ["empty", "empty", "found"]
        assert len(result) == 1

    def test_a_strategy_raising_is_treated_as_no_results(self, monkeypatch):
        def raises(source):
            raise RuntimeError("network exploded")

        def finds_something(source):
            return [{"url": "https://example.test/y", "title": "Y", "summary": "", "image_url": None, "published_at": None}]

        monkeypatch.setattr(discovery, "_STRATEGIES", (raises, finds_something))

        result = discovery.discover_source(SOURCE)
        assert len(result) == 1

    def test_all_strategies_failing_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(discovery, "_STRATEGIES", (lambda s: [], lambda s: []))
        assert discovery.discover_source(SOURCE) == []


class TestRssDiscover:
    def test_parses_real_rss_and_filters_non_article_links(self, monkeypatch):
        homepage_html = (
            '<html><head>'
            '<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
            '</head><body></body></html>'
        )
        feed_xml = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>Test Feed</title>
  <item>
    <title>A real news story about something</title>
    <link>https://example.test/2026/09/a-real-news-story-12345</link>
    <description>Some summary text here.</description>
  </item>
  <item>
    <title>Just a tag page</title>
    <link>https://example.test/tag/politics</link>
  </item>
</channel></rss>"""

        def fake_get(url, **kw):
            if url == "https://example.test/":
                return _fake_response(text=homepage_html)
            if url.endswith("/feed.xml"):
                return _fake_response(content=feed_xml.encode("utf-8"))
            return None

        monkeypatch.setattr(discovery, "_get", fake_get)

        items = discovery.rss_discover(SOURCE)
        urls = [i["url"] for i in items]
        assert "https://example.test/2026/09/a-real-news-story-12345" in urls
        assert not any("/tag/" in u for u in urls)

    def test_guessed_feed_path_returning_html_is_rejected(self, monkeypatch):
        """Regression test: a guessed /feed/ path that actually returns the
        site's normal HTML (e.g. a category/section page, not a real feed)
        must not be treated as a working RSS feed just because feedparser
        can loosely salvage some entries out of it."""
        homepage_no_link_tag = '<html><head></head><body>no feed link here</body></html>'
        html_masquerading_as_feed = '<html><body><a href="https://example.test/family/some-section">Section</a></body></html>'

        def fake_get(url, **kw):
            if url == "https://example.test/":
                return _fake_response(text=homepage_no_link_tag, content_type="text/html")
            if "/feed" in url or "/rss" in url:
                return _fake_response(text=html_masquerading_as_feed, content_type="text/html; charset=utf-8")
            return None

        monkeypatch.setattr(discovery, "_get", fake_get)

        items = discovery.rss_discover(SOURCE)
        assert items == []


class TestSitemapDiscover:
    def test_noncompliant_urlset_of_sitemaps_is_treated_as_an_index(self, monkeypatch):
        """Regression test for prajasakti.com: a top-level /sitemap.xml tagged
        <urlset> (not the spec-correct <sitemapindex>) whose <loc> entries are
        themselves other sitemap .xml files, not real pages. Must recurse
        instead of returning the nested sitemap URLs as if they were articles."""
        root_sitemap = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.test/home/sitemap.xml</loc></url>
  <url><loc>https://example.test/news/sitemap/1.xml</loc></url>
  <url><loc>https://example.test/tags/sitemap.xml</loc></url>
</urlset>"""
        news_child_sitemap = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.test/news/2026/09/a-real-article-slug-123</loc></url>
</urlset>"""

        def fake_get(url, **kw):
            if url.endswith('/robots.txt'):
                return None
            if url.endswith('/sitemap.xml') and '/news/' not in url and '/home/' not in url and '/tags/' not in url:
                return _fake_response(content=root_sitemap.encode('utf-8'))
            if url == 'https://example.test/news/sitemap/1.xml':
                return _fake_response(content=news_child_sitemap.encode('utf-8'))
            return None

        monkeypatch.setattr(discovery, '_get', fake_get)

        items = discovery.sitemap_discover(SOURCE)
        urls = [i['url'] for i in items]
        assert urls == ['https://example.test/news/2026/09/a-real-article-slug-123']
        assert not any(u.endswith('.xml') for u in urls)

    def test_epaper_edition_pages_are_rejected(self, monkeypatch):
        """E-paper/PDF edition listing pages are near-universal on Indian news
        sites and are never real article content — reject them generically,
        the same way /tag/, /category/ etc. are already rejected."""
        sitemap = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.test/epaper/newspaper/krishna-81/2026-09-08</loc></url>
  <url><loc>https://example.test/news/2026/09/story-one-abcdef</loc></url>
</urlset>"""

        def fake_get(url, **kw):
            if url.endswith('/robots.txt'):
                return None
            if url.endswith('/sitemap.xml'):
                return _fake_response(content=sitemap.encode('utf-8'))
            return None

        monkeypatch.setattr(discovery, '_get', fake_get)

        items = discovery.sitemap_discover(SOURCE)
        urls = [i['url'] for i in items]
        assert urls == ['https://example.test/news/2026/09/story-one-abcdef']

    def test_real_urlset_of_articles_is_returned_directly(self, monkeypatch):
        sitemap = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.test/news/2026/09/story-one-abcdef</loc></url>
  <url><loc>https://example.test/news/2026/09/story-two-abcdef</loc></url>
</urlset>"""

        def fake_get(url, **kw):
            if url.endswith('/robots.txt'):
                return None
            if url.endswith('/sitemap.xml'):
                return _fake_response(content=sitemap.encode('utf-8'))
            return None

        monkeypatch.setattr(discovery, '_get', fake_get)

        items = discovery.sitemap_discover(SOURCE)
        assert len(items) == 2
