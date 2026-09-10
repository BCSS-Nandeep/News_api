import json

import pytest

FIXTURE_SOURCES = [
    {
        "id": "test_telangana_telugu",
        "name": "Test Telangana Telugu Source",
        "region": "South India",
        "country": "India",
        "state": "Telangana",
        "language": "Telugu",
        "type": "tv_news",
        "base_url": "https://example-telangana.test/",
        "active": True,
    },
    {
        "id": "test_tamil_nadu",
        "name": "Test Tamil Nadu Source",
        "region": "South India",
        "country": "India",
        "state": "Tamil Nadu",
        "language": "Tamil",
        "type": "tv_news",
        "base_url": "https://example-tamilnadu.test/",
        "active": True,
    },
    {
        "id": "test_andhra_english",
        "name": "Test Andhra English Source",
        "region": "South India",
        "country": "India",
        "state": "Andhra Pradesh",
        "language": "English",
        "type": "newspaper",
        "base_url": "https://example-andhra.test/",
        "active": True,
    },
    # International sources carry an empty state — district/location detection
    # is Telangana-only, so nothing outside India gets invented location data.
    {
        "id": "test_us_english",
        "name": "Test US Source",
        "region": "North America",
        "country": "United States",
        "state": "",
        "language": "English",
        "type": "tv_news",
        "base_url": "https://example-us.test/",
        "active": True,
    },
    {
        "id": "test_uk_english",
        "name": "Test UK Source",
        "region": "Europe",
        "country": "United Kingdom",
        "state": "",
        "language": "English",
        "type": "newspaper",
        "base_url": "https://example-uk.test/",
        "active": True,
    },
    {
        "id": "test_global_agency",
        "name": "Test Global Agency",
        "region": "Global",
        "country": "International",
        "state": "",
        "language": "English",
        "type": "news_agency",
        "base_url": "https://example-global.test/",
        "active": True,
    },
    {
        "id": "test_inactive",
        "name": "Test Inactive Source",
        "region": "North India",
        "country": "India",
        "state": "Pan-India (Hindi Belt)",
        "language": "Hindi",
        "type": "newspaper",
        "base_url": "https://example-inactive.test/",
        "active": False,
    },
]

# Deliberately written the way the registry looked before `country` existed —
# proves an entry without the field still loads (Source.country defaults to '').
LEGACY_SHAPED_SOURCE = {
    "id": "test_legacy_no_country",
    "name": "Test Legacy Source",
    "region": "South India",
    "state": "Kerala",
    "language": "Malayalam",
    "type": "newspaper",
    "base_url": "https://example-legacy.test/",
    "active": True,
}


def _write_registry(tmp_path, monkeypatch, entries):
    from scraper import sources_registry

    registry_path = tmp_path / "News_URLs.json"
    registry_path.write_text(json.dumps(entries), encoding="utf-8")

    monkeypatch.setattr(sources_registry, "_REGISTRY_PATH", registry_path)
    sources_registry.reload_registry()
    return sources_registry


@pytest.fixture
def fixture_registry(tmp_path, monkeypatch):
    """Point scraper.sources_registry at a small, controlled registry file
    instead of the real 377-entry News_URLs.json, and reset it afterwards."""
    registry = _write_registry(tmp_path, monkeypatch, FIXTURE_SOURCES)
    yield registry
    registry.reload_registry()


@pytest.fixture
def legacy_registry(tmp_path, monkeypatch):
    """A registry whose entries predate the `country` field entirely."""
    registry = _write_registry(tmp_path, monkeypatch, [LEGACY_SHAPED_SOURCE])
    yield registry
    registry.reload_registry()


@pytest.fixture(autouse=True)
def clear_cache():
    """Every test starts with an empty in-memory article cache."""
    from services import cache
    cache.clear()
    yield
    cache.clear()
