from processing.keywords import match_keyword, matches_keyword, parse_keyword


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


# ── multi-word keywords: split, filter, score ─────────────────────────────────

def test_multi_word_keyword_is_split_into_terms():
    q = parse_keyword("CJP School Thik Karo")
    assert q.terms == ['cjp', 'school', 'thik', 'karo']
    assert not q.is_list
    assert q.phrases[0].text == 'cjp school thik karo'


def test_common_words_and_single_letters_are_dropped():
    assert parse_keyword("protest in the school of a town").terms == ['protest', 'school', 'town']
    assert parse_keyword("police ka action").terms == ['police', 'action']


def test_only_common_words_falls_back_rather_than_matching_everything():
    assert parse_keyword("the").terms == ['the']


def test_duplicates_and_punctuation_are_cleaned():
    # No commas here — a comma would start a second phrase (see list tests below).
    assert parse_keyword("Drugs drugs! (Hyderabad)").terms == ['drugs', 'hyderabad']


def test_possessive_s_is_dropped():
    assert parse_keyword("minister's resignation").terms == ['minister', 'resignation']


def test_quoted_text_is_a_required_phrase():
    q = parse_keyword('"thik karo" school')
    assert q.required == ['thik karo']
    assert q.terms == ['school']
    assert match_keyword("School news", "", "", q) is None, 'phrase missing'
    assert match_keyword("Thik karo, says school", "", "", q) is not None


def test_indian_script_words_are_not_split_at_vowel_signs():
    assert parse_keyword("హత్యలు పెరిగాయి").terms == ['హత్యలు', 'పెరిగాయి']


def test_partial_match_still_matches_but_scores_lower():
    q = parse_keyword("CJP School Thik Karo")
    both = match_keyword("CJP protest at school", "", "", q)
    one = match_keyword("", "", "New school building opened", q)
    assert both.matched_terms == ['cjp', 'school']
    assert one.matched_terms == ['school']
    assert both.score > one.score
    assert match_keyword("Weather update", "", "", q) is None


def test_whole_phrase_and_title_hits_break_ties():
    q = parse_keyword("school fire")
    phrase = match_keyword("", "", "a school fire broke out", q)
    apart = match_keyword("", "", "fire near the old school", q)
    in_title = match_keyword("Fire at school", "", "", q)
    assert phrase.score > apart.score, 'same terms, but the phrase appears'
    assert in_title.score > apart.score, 'same terms, but in the title'


def test_term_matching_keeps_word_start_rule():
    q = parse_keyword("kill cricket")
    assert match_keyword("Bowler's skill", "", "", q) is None
