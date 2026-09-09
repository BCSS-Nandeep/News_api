from processing.keywords import matches_keyword


def test_no_keyword_always_matches():
    assert matches_keyword("Title", "Summary", "Content", None) is True
    assert matches_keyword("Title", "Summary", "Content", "") is True


def test_keyword_matches_case_insensitively():
    assert matches_keyword("Police seize DRUGS in Hyderabad", "", "", "drugs") is True


def test_keyword_matches_in_content_field():
    assert matches_keyword("Title", "", "narcotics were recovered", "narcotics") is True


def test_keyword_no_match_returns_false():
    assert matches_keyword("Unrelated story", "about weather", "", "corruption") is False


def test_keyword_matches_telugu_text():
    assert matches_keyword("అవినీతి ఆరోపణలు", "", "", "అవినీతి") is True
