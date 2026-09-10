"""
Section fronts must never be served as articles.

Regression cover for `country=China`, which returned 76 "articles" of which 50
had no body at all — Xinhua section fronts titled "Sports", "Photos", "Europe",
"In-depth", each carrying the site's homepage meta description as its summary.

Three independent defences, tested here:
    1. is_article_url()      rejects the URL shape before anything is fetched
    2. _is_listing_page()    rejects a fetched page with no prose in it
    3. _normalize_article()  drops anything left with no extractable body
"""

from datetime import datetime, timezone

import pytest

from scraper.extraction import is_article_url, _jsonld_body, _densest_text_block
from scraper.sources_registry import Source
from services import news_service
from tests.test_extraction import soup_of

SOURCE = Source(
    id='xinhua_english', name='Xinhua English', region='Asia', state='',
    language='English', type='news_agency', base_url='https://english.news.cn/',
    active=True, country='China',
)


class TestSectionFrontUrlsRejected:
    @pytest.mark.parametrize('url', [
        # The exact URLs served as articles in the China result set.
        'https://english.news.cn/northamerica/index.htm',
        'https://english.news.cn/africa/index.htm',
        'https://english.news.cn/europe/index.htm',
        'https://english.news.cn/asiapacific/index.htm',
        'https://english.news.cn/sports/index.htm',
        'https://english.news.cn/photo/index.htm',
        'https://english.news.cn/world/index.htm',
        'https://english.news.cn/culture/index.htm',
        'https://english.news.cn/indepth/index.htm',
        'https://english.news.cn/posters/index.htm',
        'https://english.news.cn/silkroad/index.html',
        'https://english.news.cn/china-chinainaday/index.htm',
        'https://english.news.cn/xinhuanews/index.htm',
        'https://english.news.cn/list/World-americas.htm',
        'https://english.news.cn/list/World-MiddleEast.htm',
        'https://english.news.cn/list/latestnews.htm',
        'https://english.news.cn/list/china-business.htm',
        'https://english.news.cn/list/video-Shorts.htm',
        'https://english.news.cn/special/index.htm',
        'https://english.news.cn/special/202609ciftis/index.html',
        'https://www.globaltimes.cn/galleries/6333.html',
    ])
    def test_rejected(self, url):
        assert is_article_url(url) is False, url


class TestRealArticleUrlsStillAccepted:
    @pytest.mark.parametrize('url', [
        # An index.html whose directory IS the article id — CGTN's shape.
        'https://news.cgtn.com/news/2026-09-09/VHJhbnNjcmlwdDkyMzM4/index.html',
        'https://global.chinadaily.com.cn/a/202609/10/WS6aa2625ce4b06d4aa055d5ea.html',
        'https://www.chinadaily.com.cn/a/202609/10/WS6aa250dfe4b06d4aa055d572.html',
        'https://www.dawn.com/news/1234567',
        'https://x.com/news/2026/09/a-real-story-slug-12345',
    ])
    def test_accepted(self, url):
        assert is_article_url(url) is True, url

    def test_a_section_word_inside_a_slug_is_not_a_section(self):
        """The skip list matches whole path segments — an article whose slug
        merely contains one of those words must survive."""
        assert is_article_url('https://x.com/news/special-report-on-the-floods-2026') is True
        assert is_article_url('https://x.com/news/archive-footage-reveals-new-details-99') is True


class TestJsonLdBody:
    def test_reads_article_body(self):
        html = (
            '<script type="application/ld+json">'
            '{"@type":"NewsArticle","articleBody":"' + 'Reported from the scene. ' * 10 + '"}'
            '</script>'
        )
        assert 'Reported from the scene.' in _jsonld_body(soup_of(html))

    def test_reads_article_body_from_a_graph_wrapper(self):
        html = (
            '<script type="application/ld+json">'
            '{"@graph":[{"@type":"WebPage"},{"@type":"NewsArticle","articleBody":"'
            + 'Body sentence here. ' * 10 + '"}]}'
            '</script>'
        )
        assert 'Body sentence here.' in _jsonld_body(soup_of(html))

    def test_ignores_malformed_json(self):
        html = '<script type="application/ld+json">{"articleBody": not json}</script>'
        assert _jsonld_body(soup_of(html)) == ''

    def test_ignores_a_trivially_short_body(self):
        html = '<script type="application/ld+json">{"articleBody":"Share"}</script>'
        assert _jsonld_body(soup_of(html)) == ''

    def test_returns_empty_when_absent(self):
        assert _jsonld_body(soup_of('<p>nothing structured here</p>')) == ''


class TestShortStoriesSurvive:
    """A one-paragraph photo-caption story was being reported as
    'No content extracted' because the densest-block floor was 200 chars."""

    def test_single_real_paragraph_is_returned(self):
        para = (
            'Schools across China held a variety of activities to celebrate the '
            '42nd Teachers Day on September 10, honouring educators nationwide.'
        )
        # Between the prose floor and the old 200-char cut-off — exactly the
        # band that used to be discarded as "no content".
        assert 100 < len(para) < 200
        assert _densest_text_block(soup_of(f'<div><p>{para}</p></div>')) == para

    def test_nav_label_length_text_is_still_rejected(self):
        assert _densest_text_block(soup_of('<div><p>Explore more stories</p></div>')) == ''


class TestBodylessArticlesAreDropped:
    def _page(self, **over):
        page = {
            'title': 'Sports', 'description': 'Xinhua brings you headlines, photos and video.',
            'content': '', 'image': None, 'published': None, 'is_listing': False,
        }
        page.update(over)
        return page

    def _item(self):
        return {
            'url': 'https://english.news.cn/sports/index.htm',
            'title': 'Sports', 'summary': '', 'image_url': None,
            'published_at': datetime(2026, 9, 10, tzinfo=timezone.utc),
        }

    def test_page_with_no_content_is_dropped(self, monkeypatch):
        monkeypatch.setattr(news_service, 'fetch_article_page', lambda url: self._page())
        assert news_service._normalize_article(self._item(), SOURCE) is None

    def test_whitespace_only_content_is_dropped(self, monkeypatch):
        monkeypatch.setattr(news_service, 'fetch_article_page', lambda url: self._page(content='   \n  '))
        assert news_service._normalize_article(self._item(), SOURCE) is None

    def test_listing_flag_still_drops(self, monkeypatch):
        monkeypatch.setattr(
            news_service, 'fetch_article_page',
            lambda url: self._page(content='real body text', is_listing=True),
        )
        assert news_service._normalize_article(self._item(), SOURCE) is None

    def test_article_with_a_body_survives(self, monkeypatch):
        body = 'A real story body that is comfortably long enough to count as content.'
        monkeypatch.setattr(news_service, 'fetch_article_page', lambda url: self._page(content=body))
        article = news_service._normalize_article(self._item(), SOURCE)
        assert article is not None
        assert article['content'] == body
        assert article['country'] == 'China'


class TestImageFallback:
    def test_no_image_yields_an_empty_string_not_an_indian_flag(self, monkeypatch):
        """The old placeholder was an Indian flag, which is wrong on a story
        from anywhere else. An empty value lets the client omit the image."""
        monkeypatch.setattr(
            news_service, 'fetch_article_page',
            lambda url: {'title': 'T', 'description': 'D', 'content': 'Body text here.',
                         'image': None, 'published': None, 'is_listing': False},
        )
        article = news_service._normalize_article(
            {'url': 'https://english.news.cn/a/story-slug-12345.htm', 'title': 'T'}, SOURCE,
        )
        assert article['image_url'] == ''
        assert 'Flag_of_India' not in article['image_url']

    def test_page_image_is_used_when_present(self, monkeypatch):
        monkeypatch.setattr(
            news_service, 'fetch_article_page',
            lambda url: {'title': 'T', 'description': 'D', 'content': 'Body text here.',
                         'image': 'https://img.example/pic.jpg', 'published': None,
                         'is_listing': False},
        )
        article = news_service._normalize_article(
            {'url': 'https://english.news.cn/a/story-slug-12345.htm', 'title': 'T'}, SOURCE,
        )
        assert article['image_url'] == 'https://img.example/pic.jpg'
