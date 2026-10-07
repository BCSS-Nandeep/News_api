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


# ── a keyword is a whole phrase ───────────────────────────────────────────────

def test_a_keyword_is_matched_as_a_whole_phrase():
    q = parse_keyword("CJP School Thik Karo")
    assert [p.text for p in q.phrases] == ['cjp school thik karo']
    assert not q.is_list
    assert match_keyword("The CJP School Thik Karo campaign begins", "", "", q) is not None
    assert match_keyword("CJP launches School Thik Karo campaign", "", "", q) is None, "words in between break the phrase"
    assert match_keyword("New school building opened", "", "", q) is None, "one word of the phrase is not enough"
    assert match_keyword("CJP leaders visit a school", "", "", q) is None, "some words, not the phrase"
    assert match_keyword("Karo thik school CJP", "", "", q) is None, "same words, wrong order"


def test_phrase_ignores_case_and_punctuation_between_words():
    q = parse_keyword("school thik karo")
    assert match_keyword("The 'School-Thik Karo' drive", "", "", q) is not None
    assert match_keyword("", "", "SCHOOL\n THIK, KARO", q) is not None


def test_phrase_keeps_common_words():
    q = parse_keyword("school and mass education")
    assert match_keyword("Odisha School and Mass Education department", "", "", q) is not None
    assert match_keyword("Mass school education", "", "", q) is None


def test_phrase_starts_at_a_word_and_its_last_word_may_continue():
    q = parse_keyword("textbook error")
    assert match_keyword("Textbook errors found", "", "", q) is not None, "'error' → 'errors'"
    assert match_keyword("Etextbook error", "", "", q) is None, "must start at a word"


def test_possessive_is_part_of_the_phrase():
    q = parse_keyword("minister's resignation")
    assert match_keyword("Students demand Minister's resignation", "", "", q) is not None


def test_indian_script_phrase():
    q = parse_keyword("హత్యలు పెరిగాయి")
    assert match_keyword("రాష్ట్రంలో హత్యలు  పెరిగాయి", "", "", q) is not None, "extra spaces don't matter"
    assert match_keyword("పెరిగాయి హత్యలు", "", "", q) is None


def test_mixed_script_phrase_uses_substring_matching():
    q = parse_keyword("CJP హత్య")
    assert match_keyword("cjp హత్యలు", "", "", q) is not None
    assert match_keyword("CJP rally", "", "", q) is None, "the Telugu part must not be dropped"


# ── several keywords: comma-separated ─────────────────────────────────────────

def test_commas_separate_keywords():
    q = parse_keyword("CJP School Thik Karo campaign, NYCS; textbook errors\nSourav Das")
    assert q.is_list
    assert [p.text for p in q.phrases] == ['cjp school thik karo campaign', 'nycs', 'textbook errors', 'sourav das']


def test_duplicates_and_empty_items_are_dropped():
    q = parse_keyword("School  Mass, school mass, , ; NYCS, nycs")
    assert [p.text for p in q.phrases] == ['school mass', 'nycs']


def test_quotes_group_text_containing_commas():
    q = parse_keyword('"Bhubaneswar, Sep 21", NYCS')
    assert [p.text for p in q.phrases] == ['bhubaneswar, sep 21', 'nycs']
    assert match_keyword("", "", "Bhubaneswar, Sep 21 (PTI): students", q).matched_phrases == ['bhubaneswar, sep 21']


def test_list_counts_keywords_matched_and_title_hits():
    q = parse_keyword("textbook errors, NYCS, Sourav Das")
    m = match_keyword("NYCS protest grows", "", "Sourav Das spoke about textbook errors.", q)
    assert m.matched_phrases == ['textbook errors', 'nycs', 'sourav das']
    assert m.matched_terms == ['textbook', 'errors', 'nycs', 'sourav', 'das']
    assert m.title_hits == 1
    weaker = match_keyword("", "", "Sourav Das spoke.", q)
    assert weaker.matched_phrases == ['sourav das']
    assert m.score > weaker.score


def test_min_match_counts_keywords():
    q = parse_keyword("textbook errors, NYCS, Sourav Das")
    assert match_keyword("", "", "Sourav Das spoke.", q, min_match=2) is None
    assert match_keyword("", "", "Sourav Das and NYCS.", q, min_match=2) is not None
    assert match_keyword("", "", "Sourav Das, NYCS and textbook errors.", q, min_match=9) is not None, \
        "asking for more keywords than given means all of them"


def test_keyword_word_start_rule_holds_in_lists():
    q = parse_keyword("kill, cricket")
    assert match_keyword("Bowler's skill", "", "", q) is None
