"""
FastAPI route tests. The news_service functions are monkeypatched to return
fixture data — no live scraping and no network anywhere in this file.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from api.main import app
from services import news_service

# raise_server_exceptions=False: a real HTTP client (SocEye) only ever sees
# the JSON response from our exception handler, never a Python traceback —
# TestClient's default of re-raising is purely a debugging convenience that
# would make test_get_articles_service_failure_returns_valid_error fail even
# though the actual HTTP contract (500 + JSON body) is correct.
client = TestClient(app, raise_server_exceptions=False)

SAMPLE_ARTICLE = {
    'id': 'abc123',
    'title': 'Police seize narcotics in Hyderabad',
    'content': 'Full article content about a narcotics seizure.',
    'summary': 'Police seized narcotics in a raid.',
    'source': 'Test Telangana Telugu Source',
    'source_id': 'test_telangana_telugu',
    'source_url': 'https://example-telangana.test/article-1',
    'language': 'Telugu',
    'country': 'India',
    'state': 'Telangana',
    'district': 'Hyderabad',
    'location': 'Hyderabad',
    'published_at': datetime(2026, 9, 9, 8, 30, tzinfo=timezone.utc),
    'image_url': 'https://example-telangana.test/image.jpg',
}


def test_health():
    resp = client.get('/health')
    assert resp.status_code == 200
    assert resp.json() == {'status': 'ok'}


def test_ui_page_is_served_at_root():
    resp = client.get('/')
    assert resp.status_code == 200
    assert 'text/html' in resp.headers['content-type']
    assert '<title>Blura News API</title>' in resp.text


def test_sources_endpoint(fixture_registry):
    resp = client.get('/news/sources')
    assert resp.status_code == 200
    ids = {s['id'] for s in resp.json()}
    assert 'test_telangana_telugu' in ids
    assert 'test_inactive' not in ids  # inactive sources excluded


def test_sources_endpoint_filters_by_state(fixture_registry):
    resp = client.get('/news/sources', params={'state': 'Tamil Nadu'})
    assert resp.status_code == 200
    ids = [s['id'] for s in resp.json()]
    assert ids == ['test_tamil_nadu']


def test_get_articles_returns_normalized_shape(monkeypatch):
    monkeypatch.setattr(
        news_service, 'get_articles',
        lambda **kw: {'count': 1, 'limit': 20, 'offset': 0, 'articles': [SAMPLE_ARTICLE]},
    )
    resp = client.get('/news/articles')
    assert resp.status_code == 200
    body = resp.json()
    assert body['count'] == 1
    article = body['articles'][0]
    assert article['id'] == 'abc123'
    assert article['title'] == SAMPLE_ARTICLE['title']
    # no sentiment/political fields anywhere in the response
    for forbidden in ('sentiment', 'political_sentiment', 'bias', 'llm_score', 'sentiment_target'):
        assert forbidden not in article


def test_get_articles_passes_filters_through(monkeypatch):
    captured = {}

    def fake_get_articles(**kw):
        captured.update(kw)
        return {'count': 0, 'limit': 20, 'offset': 0, 'articles': []}

    monkeypatch.setattr(news_service, 'get_articles', fake_get_articles)

    resp = client.get('/news/articles', params={
        'keyword': 'drugs', 'language': 'Telugu', 'location': 'Hyderabad', 'state': 'Telangana',
    })
    assert resp.status_code == 200
    assert captured['keyword'] == 'drugs'
    assert captured['language'] == 'Telugu'
    assert captured['location'] == 'Hyderabad'
    assert captured['state'] == 'Telangana'


def test_get_articles_limit_validation():
    resp = client.get('/news/articles', params={'limit': 0})
    assert resp.status_code == 422
    resp = client.get('/news/articles', params={'limit': 1000})
    assert resp.status_code == 422


def test_get_article_by_id_found(monkeypatch):
    monkeypatch.setattr(news_service, 'get_article_by_id', lambda aid: SAMPLE_ARTICLE if aid == 'abc123' else None)
    resp = client.get('/news/articles/abc123')
    assert resp.status_code == 200
    assert resp.json()['id'] == 'abc123'


def test_get_article_by_id_not_found(monkeypatch):
    monkeypatch.setattr(news_service, 'get_article_by_id', lambda aid: None)
    resp = client.get('/news/articles/does-not-exist')
    assert resp.status_code == 404


def test_get_articles_empty_result_is_not_an_error(monkeypatch):
    monkeypatch.setattr(
        news_service, 'get_articles',
        lambda **kw: {'count': 0, 'limit': 20, 'offset': 0, 'articles': []},
    )
    resp = client.get('/news/articles', params={'state': 'NoSuchState'})
    assert resp.status_code == 200
    assert resp.json()['articles'] == []


def test_get_articles_service_failure_returns_valid_error(monkeypatch):
    def boom(**kw):
        raise RuntimeError("a source blew up")

    monkeypatch.setattr(news_service, 'get_articles', boom)
    resp = client.get('/news/articles')
    assert resp.status_code == 500
