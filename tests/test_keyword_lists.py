"""
Keyword lists: comma/semicolon/new-line separated keywords, each matched as a
whole phrase; articles rank by how many keywords they contain; min_match sets
the minimum. Uses a real investigator's list.
"""

import time
from datetime import datetime, timedelta, timezone

from processing.keywords import MAX_PHRASES, match_keyword, parse_keyword
from services import news_service

# Pasted by an analyst tracking one story (trimmed of nothing — duplicates and
# near-duplicates included on purpose).
ANALYST_LIST = (
    "CJP School Thik Karo campaign, Odisha education minister resignation protest, CJP demand resignation "
    "Odisha, Saurav Das Odisha press conference, Odisha textbook printing errors, cjp demand - education "
    "minister's resignation odisha, NYCS, Navnirman Yuva Chhatra Sangathan, cockroach janta party, education "
    "minister nityananda, minister nityananda gond, cjp backs odisha, backs odisha students, errors in school, "
    "chhatra sangathan nycs, school and mass, mass education minister, alleged errors, school textbooks, party "
    "cjp, demanding, textbook error row, error row cjp, row cjp backs, resignation of odisha, cockroach janata "
    "party, student agitation, ongoing satyagraha, odisha school, textbook errors, Demand For Minister, Sourav "
    "Das, Takes School Education Campaign To, Demands Minister, School Thik Karo, odisha students demand, "
    "textbooks in odisha, odisha received support, monday september 21, resignation of school, alleged "
    "irregularities textbook, irregularities textbook error, odisha students theprint, students theprint "
    "bhubaneswar, theprint bhubaneswar sep, bhubaneswar sep 21, sep 21 pti, textbooks cockroach janta, cjp co "
    "convenor, co convenor sourav, odisha students protest, students protest seeks, protest seeks minister, "
    "seeks minister exit, calling for accountability, accountability and education, distribution of corrected, "
    "corrected odisha textbook, errors row cockroach, row cockroach janata, janata party supports, party "
    "supports nycs, supports nycs cockroach, nycs cockroach janata, cjp takes school, thik karo campaign, "
    "odisha backing protests, resignation of education, ongoing student, cjp backed, monday extended, cjp co, "
    "officially backed, agitation calling, education reforms, odisha government, shown support, textbooks "
    "according, odisha demands, initiated"
)

STORY = (
    "Cockroach Janata Party backs Odisha students in textbook errors row",
    "The CJP has backed the NYCS agitation demanding the resignation of the education minister.",
    "Bhubaneswar, Sep 21 (PTI): The Cockroach Janata Party (CJP) on Monday extended support to the "
    "Navnirman Yuva Chhatra Sangathan (NYCS), whose student agitation over alleged errors in school "
    "textbooks is demanding the resignation of Odisha's School and Mass Education Minister Nityananda Gond. "
    "CJP co-convenor Sourav Das said the party's School Thik Karo campaign calls for accountability.",
)
GENERIC = (
    "Odisha government announces new bus routes",
    "The Odisha government said on Monday that routes are being initiated.",
    "Bhubaneswar: new routes initiated by the transport department.",
)


# ── parsing ───────────────────────────────────────────────────────────────────

def test_analyst_list_parses():
    q = parse_keyword(ANALYST_LIST)
    assert q.is_list
    assert len(q.phrases) == 80  # 80 comma-separated keywords, all distinct
    texts = [p.text for p in q.phrases]
    assert 'cjp school thik karo campaign' in texts and 'nycs' in texts and 'school and mass' in texts


def test_list_is_capped():
    q = parse_keyword(', '.join(f'term{i} word{i}' for i in range(MAX_PHRASES + 50)))
    assert len(q.phrases) == MAX_PHRASES


# ── matching ──────────────────────────────────────────────────────────────────

def test_each_keyword_must_appear_as_a_phrase():
    q = parse_keyword("textbook errors, education minister resignation")
    assert match_keyword("Textbook errors found", "", "", q).matched_phrases == ['textbook errors']
    assert match_keyword("Errors found in new textbook", "", "", q) is None, "both words, but not the phrase"


def test_list_ranks_by_keywords_matched():
    q = parse_keyword(ANALYST_LIST)
    story = match_keyword(*STORY, q)
    generic = match_keyword(*GENERIC, q)
    assert len(story.matched_phrases) == 26
    assert {'nycs', 'cockroach janata party', 'school thik karo', 'sourav das', 'textbook errors'} <= set(story.matched_phrases)
    assert generic.matched_phrases == ['odisha government', 'initiated']
    assert story.score > generic.score


def test_min_match_filters_out_weak_matches():
    q = parse_keyword(ANALYST_LIST)
    assert match_keyword(*GENERIC, q, min_match=3) is None
    assert match_keyword(*STORY, q, min_match=3) is not None


def test_list_scoring_is_fast_enough_for_long_lists():
    q = parse_keyword(ANALYST_LIST)
    body = ' '.join([STORY[2]] * 12)  # ~6 KB article body
    started = time.perf_counter()
    for _ in range(300):
        match_keyword(STORY[0], STORY[1], body, q)
    assert time.perf_counter() - started < 3.0, '300 articles x 80 keywords should take well under a request budget'


# ── end to end through get_articles ───────────────────────────────────────────

def test_get_articles_with_a_list(fixture_registry, monkeypatch):
    base = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    rows = [('generic', GENERIC, 0), ('story', STORY, 5), ('unrelated', ('Cricket', 'Match drawn', 'Rain.'), 1)]

    def fake_scrape(source):
        if source.id != 'test_telangana_telugu':
            return []
        return [{
            'id': aid, 'title': t, 'summary': s, 'content': c, 'source': source.name, 'source_id': source.id,
            'source_url': f'https://x.test/{aid}', 'language': source.language, 'country': source.country,
            'state': source.state, 'district': '', 'location': '',
            'published_at': base - timedelta(hours=h), 'image_url': '',
        } for aid, (t, s, c), h in rows]

    monkeypatch.setattr(news_service, '_scrape_source', fake_scrape)
    result = news_service.get_articles(keyword=ANALYST_LIST, source='test_telangana_telugu')
    assert [a['id'] for a in result['articles']] == ['story', 'generic'], 'story first despite being older'
    assert len(result['query_phrases']) == 80
    assert 'nycs' in result['articles'][0]['matched_phrases']

    strict = news_service.get_articles(keyword=ANALYST_LIST, source='test_telangana_telugu', min_match=3)
    assert [a['id'] for a in strict['articles']] == ['story']

    single = news_service.get_articles(keyword='nycs', source='test_telangana_telugu')
    assert single['query_phrases'] == ['nycs']
    assert single['articles'][0]['matched_phrases'] == ['nycs']
