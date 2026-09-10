"""
HTML-parsing tests for scraper/extraction.py — no network, all fixtures.
"""

from bs4 import BeautifulSoup

from scraper.extraction import (
    _densest_text_block,
    _is_listing_page,
    _parse_published,
    _strip_noise,
    is_article_url,
    prefer_https,
)

PARA = 'This is a genuine paragraph of article body text long enough to count as real content. '


def soup_of(html):
    return BeautifulSoup(html, 'html.parser')


class TestStripNoise:
    def test_does_not_destroy_page_when_body_matches_a_noise_class(self):
        """Regression: WordPress puts long class lists on <body> (e.g.
        'post-template-default ... single'). A bare [class*="ad"] matched it and
        decomposed the entire document, silently zeroing out every article."""
        html = ('<body class="post-template-default single single-post wp-embed-responsive">'
                '<div class="entry-content"><p>' + PARA * 3 + '</p></div></body>')
        s = soup_of(html)
        _strip_noise(s)
        assert s.find('div', class_='entry-content') is not None
        assert PARA.strip() in s.get_text()

    def test_does_not_strip_a_wrapper_that_holds_the_article(self):
        html = ('<div class="ad-wrapper">'
                + ''.join(f'<p>{PARA}</p>' for _ in range(4)) +
                '</div>')
        s = soup_of(html)
        _strip_noise(s)
        assert PARA.strip() in s.get_text()

    def test_still_strips_genuine_noise(self):
        html = ('<div class="entry-content"><p>' + PARA * 3 + '</p></div>'
                '<div class="social-share"><p>Follow us on twitter for more</p></div>'
                '<script>var x = 1;</script>')
        s = soup_of(html)
        _strip_noise(s)
        assert s.find('script') is None
        assert s.find('div', class_='social-share') is None
        assert PARA.strip() in s.get_text()


class TestDensestTextBlock:
    def test_finds_the_block_with_the_most_paragraph_text(self):
        html = ('<div class="sidebar"><p>' + PARA + '</p></div>'
                '<div class="mystery-cms-body">'
                + ''.join(f'<p>{PARA}</p>' for _ in range(5)) +
                '</div>')
        content = _densest_text_block(soup_of(html))
        assert len(content) > 200
        assert content.count(PARA.strip()) == 5

    def test_returns_empty_when_there_is_no_real_body(self):
        html = '<div><p>Too short.</p><p>Also short.</p></div>'
        assert _densest_text_block(soup_of(html)) == ''


# A paragraph long enough to read as an actual story rather than a nav label.
# The fixtures below need one because _is_listing_page has two independent
# signals — the WordPress body class AND whether the page contains any prose.
PROSE = (
    'Police said the seizure followed a month-long surveillance operation across '
    'three districts, and that further arrests were expected in the coming days.'
)


class TestListingDetection:
    def test_wordpress_archive_page_is_a_listing(self):
        s = soup_of(f'<body class="archive category category-crime"><p>{PROSE}</p></body>')
        assert _is_listing_page(s) is True

    def test_wordpress_single_post_is_not_a_listing(self):
        s = soup_of(
            '<body class="wp-singular post-template-default single single-post">'
            f'<p>{PROSE}</p></body>'
        )
        assert _is_listing_page(s) is False

    def test_page_without_body_classes_is_not_a_listing(self):
        assert _is_listing_page(soup_of(f'<body><p>{PROSE}</p></body>')) is False

    def test_page_with_no_paragraphs_at_all_is_a_listing(self):
        """Xinhua's section fronts (english.news.cn/sports/index.htm) are pure
        link walls with zero <p> tags and no WordPress classes — they used to
        be served as articles titled 'Sports', 'Photos', 'Europe'."""
        links = ''.join(f'<a href="/s{i}">Story {i}</a>' for i in range(50))
        assert _is_listing_page(soup_of(f'<body><div>{links}</div></body>')) is True

    def test_page_with_only_short_labels_is_a_listing(self):
        s = soup_of('<body><p>Share</p><p>Copied</p><p>EXPLORE MORE</p></body>')
        assert _is_listing_page(s) is True

    def test_a_single_real_paragraph_is_enough_to_be_an_article(self):
        """A photo-caption story is short but real — it must survive."""
        s = soup_of(f'<body><p>{PROSE}</p></body>')
        assert _is_listing_page(s) is False

    def test_link_heavy_article_is_not_a_listing(self):
        """Link count alone is a bad signal: a real article measured 272 links
        while a section front measured 20. Prose is what decides."""
        links = ''.join(f'<a href="/s{i}">More</a>' for i in range(200))
        s = soup_of(f'<body><p>{PROSE}</p><div>{links}</div></body>')
        assert _is_listing_page(s) is False


class TestPublishedTime:
    def test_reads_article_published_time(self):
        s = soup_of('<meta property="article:published_time" content="2026-09-09T08:30:00+00:00">')
        assert _parse_published(s) == '2026-09-09T08:30:00+00:00'

    def test_falls_back_to_time_element(self):
        s = soup_of('<time datetime="2026-09-01T10:00:00Z">Sept 1</time>')
        assert _parse_published(s) == '2026-09-01T10:00:00Z'

    def test_returns_none_when_page_advertises_no_date(self):
        assert _parse_published(soup_of('<p>no date here</p>')) is None


class TestIsArticleUrl:
    def test_rejects_epaper_and_listing_paths(self):
        assert is_article_url('https://x.com/epaper/newspaper/krishna-81/2026-09-08') is False
        assert is_article_url('https://x.com/tag/politics') is False
        assert is_article_url('https://x.com/category/crime') is False

    def test_accepts_a_normal_article_path(self):
        assert is_article_url('https://x.com/news/2026/09/a-real-story-slug-12345') is True

    def test_accepts_flat_permalink_articles(self):
        """Regression: siasat.com and many other WordPress sites put the whole
        headline in a single path segment. Requiring 2+ segments rejected every
        real article while letting their /news/telangana/ category pages
        through — the exact inversion that produced 'Archives' results."""
        assert is_article_url(
            'https://www.siasat.com/jamia-nizamia-files-complaint-against-local-news-3539270/') is True
        assert is_article_url(
            'https://www.siasat.com/hyderabad-metro-rail-phase-2-gets-cabinet-nod-3282667/') is True

    def test_rejects_single_segment_non_articles(self):
        assert is_article_url('https://x.com/about-us') is False
        assert is_article_url('https://x.com/contact') is False
        assert is_article_url('https://www.siasat.com/crime/') is False


class _FakeResponse:
    """Minimal stand-in for requests.Response — enough for fetch_article_page."""

    status_code = 200
    apparent_encoding = 'utf-8'

    def __init__(self, text, url):
        self.text = text
        self.url = url


class TestArticleImageResolution:
    """Regression: images referenced by a relative path were dropped entirely.

    fetch_article_page() gated every candidate on src.startswith('http'), so a
    CMS that emits "sites/default/files/..." (Drupal) or "/media/x.jpg" yielded
    image=None even when the article clearly had a lead image. Found on
    flanderstoday.eu, which serves relative paths AND has no og:image.
    """

    URL = 'https://example-news.test/some-article'

    def _fetch(self, monkeypatch, html):
        from scraper import extraction
        monkeypatch.setattr(
            extraction.requests, 'get',
            lambda *a, **kw: _FakeResponse(html, self.URL),
        )
        return extraction.fetch_article_page(self.URL)

    def test_resolves_a_relative_img_src_against_the_article_url(self, monkeypatch):
        html = ('<html><body><article>'
                '<img src="sites/default/files/webimages/babies.jpg" width="800">'
                + ''.join(f'<p>{PARA}</p>' for _ in range(4)) +
                '</article></body></html>')
        page = self._fetch(monkeypatch, html)
        assert page['image'] == 'https://example-news.test/sites/default/files/webimages/babies.jpg'

    def test_resolves_a_root_relative_img_src(self, monkeypatch):
        html = ('<html><body><article>'
                '<img src="/media/lead.jpg" width="600">'
                + ''.join(f'<p>{PARA}</p>' for _ in range(4)) +
                '</article></body></html>')
        page = self._fetch(monkeypatch, html)
        assert page['image'] == 'https://example-news.test/media/lead.jpg'

    def test_resolves_a_relative_og_image(self, monkeypatch):
        html = ('<html><head><meta property="og:image" content="/img/social.jpg">'
                '</head><body><article>'
                + ''.join(f'<p>{PARA}</p>' for _ in range(4)) +
                '</article></body></html>')
        page = self._fetch(monkeypatch, html)
        assert page['image'] == 'https://example-news.test/img/social.jpg'

    def test_leaves_an_absolute_url_untouched(self, monkeypatch):
        html = ('<html><head><meta property="og:image" content="https://cdn.other.test/a.jpg">'
                '</head><body><article>'
                + ''.join(f'<p>{PARA}</p>' for _ in range(4)) +
                '</article></body></html>')
        page = self._fetch(monkeypatch, html)
        assert page['image'] == 'https://cdn.other.test/a.jpg'

    def test_still_rejects_a_data_uri_placeholder(self, monkeypatch):
        """urljoin leaves data: URIs alone, so the http check must still drop
        them — otherwise a lazy-loading placeholder becomes the lead image."""
        html = ('<html><body><article>'
                '<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" width="800">'
                + ''.join(f'<p>{PARA}</p>' for _ in range(4)) +
                '</article></body></html>')
        page = self._fetch(monkeypatch, html)
        assert page['image'] is None


class TestPreferHttps:
    """An http:// image is blocked as mixed content on any https-served page,
    and index.html's onerror handler then removes the <img> silently — so the
    image looks missing again with nothing to explain it."""

    def test_upgrades_http_to_https(self):
        assert prefer_https('http://site.test/a.jpg') == 'https://site.test/a.jpg'

    def test_leaves_https_untouched(self):
        assert prefer_https('https://site.test/a.jpg') == 'https://site.test/a.jpg'

    def test_leaves_empty_untouched(self):
        assert prefer_https('') == ''

    def test_does_not_touch_a_host_containing_http_in_its_path(self):
        """Only the scheme is rewritten — a path segment that happens to read
        'http://' must survive intact."""
        url = 'https://site.test/redirect?to=http://other.test/a.jpg'
        assert prefer_https(url) == url
