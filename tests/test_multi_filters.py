"""
Country filtering and checkbox-style multi-select filtering.

The contract under test, end to end:
    * a filter accepts several comma-separated values (checkboxes)
    * values WITHIN one filter are ORed
    * separate filters are ANDed
    * registry-backed filters narrow which sources get scraped at all

No network anywhere — scraping is replaced by a fake that also records which
sources were asked for, which is how the "don't scrape everything" tests work.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from api.main import app
from scraper.sources_registry import parse_multi
from services import news_service

client = TestClient(app)

PUBLISHED = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def install_fake_scraper(monkeypatch, extra_by_source=None):
    """Replace live scraping with one synthetic article per source, and return
    the list that records every source id actually scraped."""
    extra_by_source = extra_by_source or {}
    scraped = []

    def fake_scrape(source):
        scraped.append(source.id)
        article = {
            'id': f'art_{source.id}',
            'title': f'{source.name} headline',
            'content': f'Body text from {source.name}.',
            'summary': f'Summary from {source.name}.',
            'source': source.name,
            'source_id': source.id,
            'source_url': f'{source.base_url}article',
            'language': source.language,
            'country': source.country,
            'state': source.state,
            'district': '',
            'location': '',
            'published_at': PUBLISHED,
            'image_url': '',
        }
        article.update(extra_by_source.get(source.id, {}))
        return [article]

    monkeypatch.setattr(news_service, '_scrape_source', fake_scrape)
    return scraped


def source_ids(result):
    return {a['source_id'] for a in result['articles']}


# ── parse_multi ────────────────────────────────────────────────────────────────

class TestParseMulti:
    def test_none_is_no_filter(self):
        assert parse_multi(None) == []

    def test_empty_string_is_no_filter(self):
        # ?country= with nothing after it must not filter everything away.
        assert parse_multi('') == []

    def test_single_value(self):
        assert parse_multi('India') == ['India']

    def test_comma_separated_values(self):
        assert parse_multi('India,United States') == ['India', 'United States']

    def test_whitespace_around_values_is_trimmed(self):
        assert parse_multi('  India , United States  ') == ['India', 'United States']

    def test_empty_selections_are_dropped(self):
        assert parse_multi('India,, ,United States,') == ['India', 'United States']

    def test_accepts_an_already_split_list(self):
        assert parse_multi(['India', 'United States']) == ['India', 'United States']

    def test_list_entries_may_themselves_be_comma_separated(self):
        # FastAPI's repeated-param form: ?country=India&country=United States,Japan
        assert parse_multi(['India', 'United States,Japan']) == ['India', 'United States', 'Japan']


# ── registry-level filtering ───────────────────────────────────────────────────

class TestRegistryCountryFilter:
    def test_single_country(self, fixture_registry):
        result = fixture_registry.list_active_sources(country='India')
        assert {s.id for s in result} == {
            'test_telangana_telugu', 'test_tamil_nadu', 'test_andhra_english',
        }

    def test_single_country_united_states(self, fixture_registry):
        result = fixture_registry.list_active_sources(country='United States')
        assert [s.id for s in result] == ['test_us_english']

    def test_multiple_countries_are_ored(self, fixture_registry):
        result = fixture_registry.list_active_sources(country='India,United States')
        assert {s.id for s in result} == {
            'test_telangana_telugu', 'test_tamil_nadu', 'test_andhra_english',
            'test_us_english',
        }

    def test_country_is_case_insensitive(self, fixture_registry):
        assert (
            {s.id for s in fixture_registry.list_active_sources(country='united states')}
            == {'test_us_english'}
        )

    def test_country_whitespace_is_normalized(self, fixture_registry):
        result = fixture_registry.list_active_sources(country='  India ,  United Kingdom ')
        assert 'test_uk_english' in {s.id for s in result}

    def test_country_excludes_inactive_sources(self, fixture_registry):
        result = fixture_registry.list_active_sources(country='India')
        assert 'test_inactive' not in {s.id for s in result}

    def test_unknown_country_returns_nothing(self, fixture_registry):
        assert fixture_registry.list_active_sources(country='Atlantis') == []

    def test_international_country_selects_global_agencies(self, fixture_registry):
        result = fixture_registry.list_active_sources(country='International')
        assert [s.id for s in result] == ['test_global_agency']


class TestRegistryMultiValueFilters:
    def test_multiple_languages_are_ored(self, fixture_registry):
        result = fixture_registry.list_active_sources(language='English,Telugu')
        assert {s.id for s in result} == {
            'test_telangana_telugu', 'test_andhra_english',
            'test_us_english', 'test_uk_english', 'test_global_agency',
        }

    def test_multiple_states_are_ored(self, fixture_registry):
        result = fixture_registry.list_active_sources(state='Telangana,Tamil Nadu')
        assert {s.id for s in result} == {'test_telangana_telugu', 'test_tamil_nadu'}

    def test_multiple_source_ids_are_ored(self, fixture_registry):
        result = fixture_registry.list_active_sources(
            source_id='test_us_english,test_uk_english',
        )
        assert {s.id for s in result} == {'test_us_english', 'test_uk_english'}

    def test_source_filter_still_accepts_a_name_substring(self, fixture_registry):
        result = fixture_registry.list_active_sources(source_id='Global Agency')
        assert [s.id for s in result] == ['test_global_agency']


class TestRegistryAndAcrossFilters:
    def test_country_and_language(self, fixture_registry):
        # (India OR United States) AND (English)
        result = fixture_registry.list_active_sources(
            country='India,United States', language='English',
        )
        assert {s.id for s in result} == {'test_andhra_english', 'test_us_english'}

    def test_country_and_state(self, fixture_registry):
        result = fixture_registry.list_active_sources(country='India', state='Telangana')
        assert [s.id for s in result] == ['test_telangana_telugu']

    def test_country_and_state_that_cannot_co_occur(self, fixture_registry):
        # Telangana is an Indian state; no US source can carry it.
        assert fixture_registry.list_active_sources(
            country='United States', state='Telangana',
        ) == []

    def test_all_four_filters_together(self, fixture_registry):
        result = fixture_registry.list_active_sources(
            country='India,United States',
            language='English,Telugu',
            state='Telangana,Andhra Pradesh',
            source_id='test_andhra_english,test_telangana_telugu,test_us_english',
        )
        assert {s.id for s in result} == {'test_andhra_english', 'test_telangana_telugu'}


class TestRegistryBackwardCompatibility:
    def test_existing_keyword_calls_are_unchanged(self, fixture_registry):
        assert [s.id for s in fixture_registry.list_active_sources(state='Telangana')] == [
            'test_telangana_telugu'
        ]

    def test_no_filters_returns_every_active_source(self, fixture_registry):
        result = fixture_registry.list_active_sources()
        assert len(result) == 6
        assert 'test_inactive' not in {s.id for s in result}

    def test_entry_without_a_country_field_still_loads(self, legacy_registry):
        source = legacy_registry.get_source('test_legacy_no_country')
        assert source is not None
        assert source.country == ''

    def test_entry_without_a_country_field_is_skipped_by_country_filter(self, legacy_registry):
        assert legacy_registry.list_active_sources(country='India') == []
        assert len(legacy_registry.list_active_sources()) == 1


# ── service-level filtering ────────────────────────────────────────────────────

class TestServiceCandidateSelection:
    def test_narrow_country_filter_only_scrapes_that_country(self, fixture_registry, monkeypatch):
        scraped = install_fake_scraper(monkeypatch)
        news_service.get_articles(country='United States')
        assert scraped == ['test_us_english']

    def test_country_filter_never_touches_indian_sources(self, fixture_registry, monkeypatch):
        scraped = install_fake_scraper(monkeypatch)
        news_service.get_articles(country='United Kingdom', language='English')
        assert 'test_telangana_telugu' not in scraped
        assert 'test_tamil_nadu' not in scraped

    def test_state_filter_focuses_on_that_state(self, fixture_registry, monkeypatch):
        scraped = install_fake_scraper(monkeypatch)
        news_service.get_articles(country='India', state='Telangana')
        assert scraped == ['test_telangana_telugu']

    def test_source_filter_scrapes_only_the_selected_sources(self, fixture_registry, monkeypatch):
        scraped = install_fake_scraper(monkeypatch)
        news_service.get_articles(source='test_us_english,test_global_agency')
        assert sorted(scraped) == ['test_global_agency', 'test_us_english']

    def test_unfiltered_request_still_honours_the_safety_limit(self, fixture_registry, monkeypatch):
        scraped = install_fake_scraper(monkeypatch)
        monkeypatch.setattr(news_service, '_MAX_SOURCES_PER_REQUEST', 2)
        news_service.get_articles()
        assert len(scraped) == 2


class TestServiceArticleFiltering:
    def test_articles_carry_the_source_country(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch)
        result = news_service.get_articles(country='United States')
        assert [a['country'] for a in result['articles']] == ['United States']

    def test_single_country(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch)
        result = news_service.get_articles(country='India')
        assert source_ids(result) == {
            'test_telangana_telugu', 'test_tamil_nadu', 'test_andhra_english',
        }

    def test_multiple_countries_are_ored(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch)
        result = news_service.get_articles(country='India,United States')
        assert {a['country'] for a in result['articles']} == {'India', 'United States'}
        assert result['count'] == 4

    def test_multiple_languages_are_ored(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch)
        result = news_service.get_articles(language='English,Telugu')
        assert {a['language'] for a in result['articles']} == {'English', 'Telugu'}
        assert 'test_tamil_nadu' not in source_ids(result)

    def test_multiple_states_are_ored(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch)
        result = news_service.get_articles(state='Telangana,Andhra Pradesh')
        assert source_ids(result) == {'test_telangana_telugu', 'test_andhra_english'}

    def test_multiple_sources_are_ored(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch)
        result = news_service.get_articles(
            source='test_us_english,test_uk_english,test_global_agency',
        )
        assert source_ids(result) == {
            'test_us_english', 'test_uk_english', 'test_global_agency',
        }

    def test_and_between_country_and_language(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch)
        # (India OR United States) AND (English) — the Telugu and Tamil Indian
        # sources drop out even though their country was selected.
        result = news_service.get_articles(country='India,United States', language='English')
        assert source_ids(result) == {'test_andhra_english', 'test_us_english'}

    def test_and_between_three_filter_categories(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch)
        result = news_service.get_articles(
            country='India,United States',
            language='English,Telugu',
            state='Telangana,Andhra Pradesh',
        )
        assert source_ids(result) == {'test_telangana_telugu', 'test_andhra_english'}

    def test_all_filters_together(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch)
        result = news_service.get_articles(
            country='India,United States',
            language='English,Telugu',
            state='Telangana,Andhra Pradesh',
            source='test_andhra_english,test_us_english',
        )
        assert source_ids(result) == {'test_andhra_english'}

    def test_contradictory_filters_return_an_empty_page_not_an_error(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch)
        result = news_service.get_articles(country='United States', language='Telugu')
        assert result == {'count': 0, 'limit': 20, 'offset': 0, 'articles': []}

    def test_keyword_combines_with_country(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch)
        assert news_service.get_articles(country='United States', keyword='Test US')['count'] == 1
        assert news_service.get_articles(country='United States', keyword='nonexistent')['count'] == 0


class TestServiceDistrictAndLocation:
    """District detection stays Telangana-only — nothing outside India gets
    invented location data, so these tests supply it explicitly."""

    EXTRAS = {
        'test_telangana_telugu': {'district': 'Hyderabad', 'location': 'Hyderabad'},
        'test_andhra_english': {'district': 'Karimnagar', 'location': 'Karimnagar'},
        'test_us_english': {'location': 'New York'},
    }

    def test_multiple_districts_are_ored(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch, self.EXTRAS)
        result = news_service.get_articles(district='Hyderabad,Karimnagar')
        assert source_ids(result) == {'test_telangana_telugu', 'test_andhra_english'}

    def test_district_ands_with_country_and_state(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch, self.EXTRAS)
        result = news_service.get_articles(
            country='India', state='Telangana', district='Hyderabad,Karimnagar',
        )
        assert source_ids(result) == {'test_telangana_telugu'}

    def test_location_combines_with_language(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch, self.EXTRAS)
        result = news_service.get_articles(language='English', location='Hyderabad')
        # The Hyderabad article is Telugu, so English AND Hyderabad matches nothing.
        assert result['count'] == 0

    def test_multiple_locations_are_ored_across_countries(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch, self.EXTRAS)
        result = news_service.get_articles(
            language='English,Telugu', location='Hyderabad,New York',
        )
        assert source_ids(result) == {'test_telangana_telugu', 'test_us_english'}

    def test_location_ands_with_country(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch, self.EXTRAS)
        result = news_service.get_articles(
            country='India', language='Telugu', location='Hyderabad',
        )
        assert source_ids(result) == {'test_telangana_telugu'}


# ── API-level ──────────────────────────────────────────────────────────────────

class TestSourcesEndpointCountry:
    def test_sources_include_a_country_field(self, fixture_registry):
        body = client.get('/news/sources').json()
        assert all('country' in s for s in body)

    def test_sources_expose_every_field_the_checkbox_lists_need(self, fixture_registry):
        body = client.get('/news/sources').json()
        assert set(body[0]) == {
            'id', 'name', 'region', 'country', 'state', 'language',
            'type', 'base_url', 'active',
        }

    def test_filter_by_single_country(self, fixture_registry):
        body = client.get('/news/sources', params={'country': 'India'}).json()
        assert {s['country'] for s in body} == {'India'}

    def test_filter_by_country_united_states(self, fixture_registry):
        body = client.get('/news/sources', params={'country': 'United States'}).json()
        assert [s['id'] for s in body] == ['test_us_english']

    def test_filter_by_multiple_countries(self, fixture_registry):
        body = client.get('/news/sources', params={'country': 'India,United States'}).json()
        assert {s['country'] for s in body} == {'India', 'United States'}

    def test_country_and_language_together(self, fixture_registry):
        body = client.get(
            '/news/sources', params={'country': 'United Kingdom', 'language': 'English'},
        ).json()
        assert [s['id'] for s in body] == ['test_uk_english']

    def test_filter_by_multiple_languages(self, fixture_registry):
        body = client.get('/news/sources', params={'language': 'English,Telugu'}).json()
        assert {s['language'] for s in body} == {'English', 'Telugu'}

    def test_filter_by_multiple_source_ids(self, fixture_registry):
        body = client.get(
            '/news/sources', params={'source': 'test_us_english,test_global_agency'},
        ).json()
        assert {s['id'] for s in body} == {'test_us_english', 'test_global_agency'}

    def test_checkbox_lists_are_derivable_from_the_response(self, fixture_registry):
        body = client.get('/news/sources').json()
        assert {s['country'] for s in body} == {
            'India', 'United States', 'United Kingdom', 'International',
        }


class TestArticlesEndpointMultiSelect:
    def test_articles_response_includes_country(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch)
        body = client.get('/news/articles', params={'country': 'United States'}).json()
        assert body['articles'][0]['country'] == 'United States'

    def test_multi_country_query(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch)
        body = client.get('/news/articles', params={'country': 'India,United States'}).json()
        assert {a['country'] for a in body['articles']} == {'India', 'United States'}

    def test_multi_language_query(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch)
        body = client.get('/news/articles', params={'language': 'English,Telugu'}).json()
        assert {a['language'] for a in body['articles']} == {'English', 'Telugu'}

    def test_multi_state_query(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch)
        body = client.get(
            '/news/articles', params={'state': 'Telangana,Andhra Pradesh'},
        ).json()
        assert {a['source_id'] for a in body['articles']} == {
            'test_telangana_telugu', 'test_andhra_english',
        }

    def test_multi_source_query(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch)
        body = client.get(
            '/news/articles', params={'source': 'test_us_english,test_uk_english'},
        ).json()
        assert {a['source_id'] for a in body['articles']} == {
            'test_us_english', 'test_uk_english',
        }

    def test_the_full_frontend_checkbox_scenario(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch)
        body = client.get('/news/articles', params={
            'country': 'India,United States',
            'language': 'English,Telugu',
            'state': 'Telangana,Andhra Pradesh',
            'source': 'test_andhra_english,test_telangana_telugu,test_us_english',
        }).json()
        assert {a['source_id'] for a in body['articles']} == {
            'test_andhra_english', 'test_telangana_telugu',
        }

    def test_empty_country_param_does_not_filter_anything_out(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch)
        body = client.get('/news/articles', params={'country': ''}).json()
        assert body['count'] == 6

    def test_pagination_still_applies_with_multi_filters(self, fixture_registry, monkeypatch):
        install_fake_scraper(monkeypatch)
        body = client.get('/news/articles', params={
            'country': 'India,United States', 'limit': 2, 'offset': 0,
        }).json()
        assert body['count'] == 4
        assert len(body['articles']) == 2

    @pytest.mark.parametrize('endpoint', ['/news/country', '/news/language', '/news/state', '/news/location'])
    def test_no_per_filter_endpoints_were_added(self, endpoint):
        assert client.get(endpoint).status_code == 404


# ── the real registry ──────────────────────────────────────────────────────────

class TestShippedRegistry:
    def test_every_source_has_a_country(self):
        from scraper import sources_registry

        sources_registry.reload_registry()
        sources = sources_registry.list_all_sources()
        missing = [s.id for s in sources if not s.country]
        assert missing == []

    def test_indian_sources_are_still_present(self):
        from scraper import sources_registry

        sources_registry.reload_registry()
        by_id = {s.id: s for s in sources_registry.list_all_sources()}
        for source_id in (
            'eenadu', 'sakshi', 'the_hindu', 'times_of_india', 'ndtv',
            'india_today', 'deccan_herald', 'news18', 'the_telegraph',
        ):
            assert source_id in by_id, source_id
            assert by_id[source_id].country == 'India'

    def test_international_sources_are_registered(self):
        from scraper import sources_registry

        sources_registry.reload_registry()
        by_id = {s.id: s for s in sources_registry.list_all_sources()}
        assert by_id['bbc_news'].country == 'United Kingdom'
        assert by_id['cnn'].country == 'United States'
        assert by_id['nhk_world'].country == 'Japan'
        assert by_id['reuters'].country == 'International'
        assert by_id['reuters'].region == 'Global'

    def test_international_sources_are_english_editions(self):
        from scraper import sources_registry

        sources_registry.reload_registry()
        for source in sources_registry.list_all_sources():
            if source.country != 'India':
                assert source.language == 'English', source.id

    def test_country_filter_works_against_the_real_registry(self):
        from scraper import sources_registry

        sources_registry.reload_registry()
        result = sources_registry.list_active_sources(country='Japan,United Kingdom')
        countries = {s.country for s in result}
        assert countries == {'Japan', 'United Kingdom'}
        assert 'japan_times' in {s.id for s in result}
        assert 'bbc_news' in {s.id for s in result}

    def test_sources_that_resolve_to_a_non_english_edition_stay_inactive(self):
        """NHK World and DW auto-discover their Arabic/Amharic editions through
        every URL variant tried, so activating them would file non-English
        articles under language=English. They stay registered but inactive —
        see the summary notes; flipping `active` is all it takes if their feed
        discovery is fixed."""
        from scraper import sources_registry

        sources_registry.reload_registry()
        by_id = {s.id: s for s in sources_registry.list_all_sources()}
        for source_id in ('nhk_world', 'deutsche_welle'):
            assert source_id in by_id, source_id
            assert by_id[source_id].active is False, source_id
        active_ids = {s.id for s in sources_registry.list_active_sources()}
        assert 'nhk_world' not in active_ids

    def test_source_ids_are_unique(self):
        from scraper import sources_registry

        sources_registry.reload_registry()
        ids = [s.id for s in sources_registry.list_all_sources()]
        assert len(ids) == len(set(ids))
