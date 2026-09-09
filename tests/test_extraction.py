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


class TestListingDetection:
    def test_wordpress_archive_page_is_a_listing(self):
        s = soup_of('<body class="archive category category-crime"><p>x</p></body>')
        assert _is_listing_page(s) is True

    def test_wordpress_single_post_is_not_a_listing(self):
        s = soup_of('<body class="wp-singular post-template-default single single-post"><p>x</p></body>')
        assert _is_listing_page(s) is False

    def test_page_without_body_classes_is_not_a_listing(self):
        assert _is_listing_page(soup_of('<body><p>x</p></body>')) is False


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
