"""
A keyword through get_articles: only articles containing the whole phrase
match; with several comma-separated keywords, articles containing more of them
rank first, and each article reports which keywords it contains.
Scraping is faked — no network.
"""

from datetime import datetime, timedelta, timezone

from services import news_service

BASE = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)

TEXTS = [
    # (id, title, content, hours ago)
    ('school_only', 'New school building opened', 'Classes begin.', 0),
    ('phrase_in_body', 'Campaign launched', 'Workers asked the CJP School Thik Karo team to help.', 5),
    ('phrase_in_title', 'CJP School Thik Karo campaign reaches Cuttack', 'Rally held.', 6),
    ('nycs', 'NYCS students protest', 'Agitation continues.', 3),
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


def ids(result):
    return [a['id'] for a in result['articles']]


def test_one_keyword_matches_only_the_whole_phrase(fixture_registry, monkeypatch):
    install(monkeypatch)
    result = news_service.get_articles(keyword='CJP School Thik Karo', source='test_telangana_telugu')
    assert ids(result) == ['phrase_in_title', 'phrase_in_body'], "title hit first; 'school' alone excluded"
    assert result['query_phrases'] == ['cjp school thik karo']
    assert result['articles'][0]['matched_phrases'] == ['cjp school thik karo']
    assert result['articles'][0]['matched_terms'] == ['cjp', 'school', 'thik', 'karo']


def test_several_keywords_rank_by_how_many_match(fixture_registry, monkeypatch):
    install(monkeypatch)
    result = news_service.get_articles(keyword='CJP School Thik Karo, campaign, NYCS', source='test_telangana_telugu')
    assert ids(result) == ['phrase_in_title', 'phrase_in_body', 'nycs']
    assert result['articles'][0]['matched_phrases'] == ['cjp school thik karo', 'campaign']
    scores = [a['match_score'] for a in result['articles']]
    assert scores == sorted(scores, reverse=True)
    strict = news_service.get_articles(keyword='CJP School Thik Karo, campaign, NYCS', source='test_telangana_telugu', min_match=2)
    assert ids(strict) == ['phrase_in_title', 'phrase_in_body']


def test_without_keyword_order_is_newest_first(fixture_registry, monkeypatch):
    install(monkeypatch)
    result = news_service.get_articles(source='test_telangana_telugu')
    assert ids(result) == ['school_only', 'no_match', 'nycs', 'phrase_in_body', 'phrase_in_title']
    assert all(a['match_score'] == 0 and a['matched_phrases'] == [] for a in result['articles'])
    assert result['query_phrases'] == []


def test_ranking_does_not_modify_the_cached_articles(fixture_registry, monkeypatch):
    install(monkeypatch)
    news_service.get_articles(keyword='school', source='test_telangana_telugu')
    from services import cache
    assert all('matched_terms' not in a for a in cache.get('test_telangana_telugu'))


def test_ranked_pages_do_not_overlap(fixture_registry, monkeypatch):
    install(monkeypatch)
    pages = [news_service.get_articles(keyword='CJP School Thik Karo, campaign, NYCS', source='test_telangana_telugu',
                                       limit=1, offset=o)['articles'][0]['id'] for o in range(3)]
    assert pages == ['phrase_in_title', 'phrase_in_body', 'nycs']
