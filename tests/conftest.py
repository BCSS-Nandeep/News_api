import json

import pytest

FIXTURE_SOURCES = [
    {
        "id": "test_telangana_telugu",
        "name": "Test Telangana Telugu Source",
        "region": "South India",
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
        "state": "Tamil Nadu",
        "language": "Tamil",
        "type": "tv_news",
        "base_url": "https://example-tamilnadu.test/",
        "active": True,
    },
    {
        "id": "test_inactive",
        "name": "Test Inactive Source",
        "region": "North India",
        "state": "Pan-India (Hindi Belt)",
        "language": "Hindi",
        "type": "newspaper",
        "base_url": "https://example-inactive.test/",
        "active": False,
    },
]


@pytest.fixture
def fixture_registry(tmp_path, monkeypatch):
    """Point scraper.sources_registry at a small, controlled registry file
    instead of the real 173-entry News_URLs.json, and reset it afterwards."""
    from scraper import sources_registry

    registry_path = tmp_path / "News_URLs.json"
    registry_path.write_text(json.dumps(FIXTURE_SOURCES), encoding="utf-8")

    monkeypatch.setattr(sources_registry, "_REGISTRY_PATH", registry_path)
    sources_registry.reload_registry()
    yield sources_registry
    sources_registry.reload_registry()


@pytest.fixture(autouse=True)
def clear_cache():
    """Every test starts with an empty in-memory article cache."""
    from services import cache
    cache.clear()
    yield
    cache.clear()
