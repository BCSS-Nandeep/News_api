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


def test_keyword_matches_word_forms_from_its_start():
    assert matches_keyword("Three killed in hit-and-run", "", "", "kill") is True
    assert matches_keyword("Man arrested for killing", "", "", "kill") is True
    assert matches_keyword("", "", "(Killers) still at large", "kill") is True


def test_keyword_does_not_match_inside_another_word():
    # The reported false positive: 'kill' found in a cricket story via 'skill'.
    assert matches_keyword("Raina praises Bhuvneshwar's skill", "", "skilled bowler", "kill") is False
    assert matches_keyword("Grape harvest begins", "", "", "rape") is False


def test_multi_word_and_punctuated_keywords():
    assert matches_keyword("Truck in hit-and-run case", "", "", "hit-and-run") is True
    assert matches_keyword("", "", "Covid-19 cases rise", "covid-19") is True
    assert matches_keyword("", "", "the drug racket busted", "drug racket") is True


def test_indian_script_keyword_still_matches_inside_compound_words():
    # Telugu suffixes attach directly; a word-start rule must not break this.
    assert matches_keyword("హత్యలు పెరిగాయి", "", "", "హత్య") is True
    assert matches_keyword("ఆసుపత్రిలో హత్యాయత్నం", "", "", "హత్య") is True
