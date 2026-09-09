from processing.location import resolve_location, detect_district


def test_detect_district_finds_earliest_alias():
    text = "An event took place in Warangal, with a reaction from Karimnagar."
    result = detect_district(text)
    assert result["location_found"] is True
    assert result["district"] == "Warangal"


def test_detect_district_no_match():
    result = detect_district("A story about something entirely unrelated.")
    assert result["location_found"] is False


def test_resolve_location_telangana_source_with_district_match():
    loc = resolve_location("Telangana", "Big news happening in Khammam today.")
    assert loc["state"] == "Telangana"
    assert loc["district"] == "Khammam"
    assert loc["location"] == "Khammam"


def test_resolve_location_telangana_source_no_district_match():
    loc = resolve_location("Telangana", "A story with no known district mentioned.")
    assert loc["state"] == "Telangana"
    assert loc["district"] == ""
    assert loc["location"] == ""


def test_resolve_location_non_telangana_source_has_no_district_detection():
    loc = resolve_location("Tamil Nadu", "Something happened in Warangal.")
    assert loc["state"] == "Tamil Nadu"
    assert loc["district"] == ""
    assert loc["location"] == ""


def test_resolve_location_combined_state_value_still_detects_telangana():
    loc = resolve_location("Andhra Pradesh & Telangana", "Reported from Nalgonda district.")
    assert loc["district"] == "Nalgonda"
