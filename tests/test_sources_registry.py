def test_list_active_sources_excludes_inactive(fixture_registry):
    active = fixture_registry.list_active_sources()
    ids = {s.id for s in active}
    assert "test_telangana_telugu" in ids
    assert "test_tamil_nadu" in ids
    assert "test_inactive" not in ids


def test_list_active_sources_filters_by_state_substring(fixture_registry):
    result = fixture_registry.list_active_sources(state="Telangana")
    assert [s.id for s in result] == ["test_telangana_telugu"]


def test_list_active_sources_filters_by_language_case_insensitive(fixture_registry):
    result = fixture_registry.list_active_sources(language="tamil")
    assert [s.id for s in result] == ["test_tamil_nadu"]


def test_list_active_sources_combines_filters(fixture_registry):
    result = fixture_registry.list_active_sources(state="Telangana", language="Tamil")
    assert result == []


def test_get_source_by_id(fixture_registry):
    source = fixture_registry.get_source("test_tamil_nadu")
    assert source is not None
    assert source.name == "Test Tamil Nadu Source"


def test_get_source_unknown_id_returns_none(fixture_registry):
    assert fixture_registry.get_source("does_not_exist") is None
