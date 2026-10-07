"""
Multi-word keyword search through get_articles: articles containing more of
the terms rank first, partial matches still appear, and each article reports
which terms it matched. Scraping is faked — no network.
"""

from datetime import datetime, timedelta, timezone

from services import news_service

BASE = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)

TEXTS = [
    # (id, title, content, hours ago) — newest first in registry order
    ('one_term_newest', 'New school building opened', 'Classes begin.', 0),
    ('all_terms', 'CJP school thik karo campaign', 'Workers asked to thik karo the school.', 5),
    ('two_terms', 'CJP leaders visit', 'Visited a school in the district.', 3),
    ('no_match', 'Weather update', 'Rain expected.', 1),
]


def install(monkeypatch):
    def fake_scrape(source):
        if source.id != 'test_telangana_telugu':
            return []
        return [{
            'id': aid, 'title': title, 'content': content, 'summary': '',
            'source': source.name, 'source_id': source.id, 'source_url': f'https://x.test/{aid}',
            'language': source.language, 'country': source.country, 'state': source.state,
            'district': '', 'location': '', 'published_at': BASE - timedelta(hours=hours),
            'image_url': '',
        } for aid, title, content, hours in TEXTS]

    monkeypatch.setattr(news_service, '_scrape_source', fake_scrape)


def test_more_matched_terms_rank_first_and_partial_matches_still_show(fixture_registry, monkeypatch):
    install(monkeypatch)
    result = news_service.get_articles(keyword='CJP School Thik Karo', source='test_telangana_telugu')

    assert [a['id'] for a in result['articles']] == ['all_terms', 'two_terms', 'one_term_newest']
    assert result['count'] == 3, 'the article matching no term is excluded'
    assert result['query_terms'] == ['cjp', 'school', 'thik', 'karo']
    assert result['articles'][0]['matched_terms'] == ['cjp', 'school', 'thik', 'karo']
    assert result['articles'][2]['matched_terms'] == ['school']
    scores = [a['match_score'] for a in result['articles']]
    assert scores == sorted(scores, reverse=True)


def test_without_keyword_order_is_newest_first(fixture_registry, monkeypatch):
    install(monkeypatch)
    result = news_service.get_articles(source='test_telangana_telugu')
    assert [a['id'] for a in result['articles']] == ['one_term_newest', 'no_match', 'two_terms', 'all_terms']
    assert all(a['match_score'] == 0 and a['matched_terms'] == [] for a in result['articles'])
    assert result['query_terms'] == []


def test_ranking_does_not_modify_the_cached_articles(fixture_registry, monkeypatch):
    install(monkeypatch)
    news_service.get_articles(keyword='school', source='test_telangana_telugu')
    from services import cache
    assert all('matched_terms' not in a for a in cache.get('test_telangana_telugu'))


def test_quoted_phrase_is_required(fixture_registry, monkeypatch):
    install(monkeypatch)
    result = news_service.get_articles(keyword='"thik karo"', source='test_telangana_telugu')
    assert [a['id'] for a in result['articles']] == ['all_terms']


def test_ranked_pages_do_not_overlap(fixture_registry, monkeypatch):
    install(monkeypatch)
    pages = [news_service.get_articles(keyword='CJP School Thik Karo', source='test_telangana_telugu',
                                       limit=1, offset=o)['articles'][0]['id'] for o in range(3)]
    assert pages == ['all_terms', 'two_terms', 'one_term_newest']
