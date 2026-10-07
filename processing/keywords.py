"""
Generic, caller-supplied keyword matching and ranking for the News API.

The keyword comes from the request — "drugs", "school protest Bhubaneswar",
anything — rather than a fixed server-side topic list, so SocEye decides what
it's looking for. No default keyword requirement: an article always matches
when no `keyword` filter is supplied.

Each keyword is matched as a whole phrase, exactly as written:
"CJP School Thik Karo" matches only articles containing that phrase, not ones
that merely mention "school". Several keywords are separated by commas,
semicolons or new lines: an article matches when it contains any of them, and
articles containing more of them rank higher. `min_match` sets the minimum
number of keywords an article must contain. Double quotes only group text
that itself contains a comma.

Matching ignores case and the punctuation between words ("School-Thik Karo"
matches "school thik karo"), and a Latin-script keyword starts at a word
boundary ("kill" finds "killed", not "skill").
"""

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List, Optional

MAX_PHRASES = 150          # keywords per list
MAX_KEYWORD_LENGTH = 6000  # characters; keeps GET URLs under proxy limits

_QUOTED = re.compile(r'["“”]([^"“”]*)["“”]')
_ITEM_SEPARATORS = re.compile(r'[,;\n\r]+')
_SPACES = re.compile(r'\s+')
_NON_WORD = re.compile(r'[^a-z0-9]+')
_EDGE_PUNCTUATION = ' .,;:!?-_*#@&\'"“”‘’()[]{}'


def normalize_text(text: str) -> str:
    """NFKC-normalize + casefold so Telugu/Hindi/English/other Indian-script
    text compares correctly regardless of composed/decomposed form or case."""
    if not text:
        return ''
    return unicodedata.normalize('NFKC', text).casefold()


def _is_latin(phrase: str) -> bool:
    """Plain ASCII keywords use the word-stream match; anything with Indian-script
    (or other non-ASCII) letters falls back to substring matching."""
    return phrase.isascii()


def ascii_words(text: str) -> str:
    """Normalized text as ' word word … ' — every run of non-[a-z0-9] becomes one
    space. Latin keywords are matched against this, so "starts a word" and
    "punctuation between words doesn't matter" are a plain substring test. The
    same rule runs in Postgres as a tsvector phrase query (saga-police
    news.keywords.js)."""
    return f" {_NON_WORD.sub(' ', text).strip()} "


def spaced(text: str) -> str:
    """Normalized text with whitespace runs collapsed, for non-Latin keywords."""
    return _SPACES.sub(' ', text)


@lru_cache(maxsize=8192)
def _latin_needle(phrase: str) -> str:
    return ' ' + _NON_WORD.sub(' ', phrase).strip()


def contains_phrase(phrase: str, text: str, words: str) -> bool:
    """`phrase` and `text` normalized; `words` = ascii_words(text)."""
    if not phrase:
        return False
    if _is_latin(phrase):
        needle = _latin_needle(phrase)
        return needle != ' ' and needle in words
    return phrase in text


@dataclass
class Phrase:
    text: str          # normalized keyword, as typed (display + matching)
    words: List[str]   # its words, for highlighting


@dataclass
class KeywordQuery:
    phrases: List[Phrase] = field(default_factory=list)
    terms: List[str] = field(default_factory=list)  # every distinct word, in order

    @property
    def is_list(self) -> bool:
        return len(self.phrases) > 1

    @property
    def empty(self) -> bool:
        return not self.phrases


def _split_items(text: str) -> List[str]:
    """Split on commas/semicolons/new lines, except inside double quotes."""
    items: List[str] = []
    last = 0
    for m in _QUOTED.finditer(text):
        items.extend(_ITEM_SEPARATORS.split(text[last:m.start()]))
        items.append(m.group(1))
        last = m.end()
    items.extend(_ITEM_SEPARATORS.split(text[last:]))
    return items


def parse_keyword(keyword: Optional[str]) -> KeywordQuery:
    if not keyword or not keyword.strip():
        return KeywordQuery()
    phrases: List[Phrase] = []
    seen = set()
    for raw in _split_items(normalize_text(keyword)):
        text = spaced(raw).strip(_EDGE_PUNCTUATION)
        if not text:
            continue
        key = _latin_needle(text) if _is_latin(text) else text
        if key.strip() == '' or key in seen:
            continue  # punctuation only, or a duplicate ("School  Mass" = "school mass")
        seen.add(key)
        words = _NON_WORD.sub(' ', text).split() if _is_latin(text) else text.split()
        phrases.append(Phrase(text=text, words=words))
        if len(phrases) == MAX_PHRASES:
            break
    # Computed once here: match_keyword runs per article, often hundreds per request.
    terms = list(dict.fromkeys(w for p in phrases for w in p.words))
    return KeywordQuery(phrases=phrases, terms=terms)


@dataclass
class KeywordMatch:
    matched_phrases: List[str]   # the keywords found
    matched_terms: List[str]     # their words (for highlighting)
    title_hits: int              # matched keywords that are in the title

    @property
    def score(self) -> int:
        """Keywords matched, then keywords in the title (title_hits <= 150 < 1000)."""
        return len(self.matched_phrases) * 1000 + self.title_hits

    @property
    def sort_key(self):
        return (len(self.matched_phrases), self.title_hits)


def match_keyword(
    title: str, summary: str, content: str, query: KeywordQuery, min_match: int = 1,
) -> Optional[KeywordMatch]:
    """None when the article contains fewer than `min_match` (at least one) of the
    keywords. An empty query matches everything with score 0."""
    if query.empty:
        return KeywordMatch([], [], 0)

    norm_title = normalize_text(title or '')
    text = spaced(f"{norm_title} {normalize_text(summary or '')} {normalize_text(content or '')}")
    words, title_text = ascii_words(text), spaced(norm_title)
    title_words = ascii_words(title_text)

    matched = [p for p in query.phrases if contains_phrase(p.text, text, words)]
    if len(matched) < min(max(1, min_match), len(query.phrases)):
        return None
    return KeywordMatch(
        matched_phrases=[p.text for p in matched],
        matched_terms=list(dict.fromkeys(w for p in matched for w in p.words)),
        title_hits=sum(1 for p in matched if contains_phrase(p.text, title_text, title_words)),
    )


def matches_keyword(title: str, summary: str, content: str, keyword: Optional[str] = None) -> bool:
    return match_keyword(title, summary, content, parse_keyword(keyword)) is not None
