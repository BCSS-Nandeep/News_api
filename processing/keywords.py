"""
Generic, caller-supplied keyword matching and ranking for the News API.

The keyword comes from the request — "drugs", "narcotics", "corruption",
anything — rather than a fixed server-side topic list, so SocEye decides what
it's looking for. No default keyword requirement: an article always matches
when no `keyword` filter is supplied.

Two shapes of keyword:

* One phrase, e.g. "CJP School Thik Karo": its words are matched
  independently and articles rank by how many they contain (then whether the
  whole phrase appears, then words in the title). One word is enough to match.

* A list of phrases separated by commas, semicolons or new lines, e.g.
  "CJP School Thik Karo campaign, NYCS, textbook errors": an article matches a
  phrase when it contains ALL of that phrase's words (anywhere, any order), and
  articles rank by how many phrases they match. Requiring every word of a
  phrase keeps generic words in a long list ("odisha", "school") from matching
  almost everything.

`min_match` raises the bar: the minimum number of words (one phrase) or
phrases (a list) an article must match. Text in "double quotes" is an exact
phrase every result must contain.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List, Optional

# Very common English and romanized-Hindi words. On their own they match almost
# every article, so they would drown the ranking in noise.
STOP_WORDS = frozenset("""
a an the of in on at to for and or is are was were be been by with from as
it its this that these those into about over after before than then there
their his her he she they we you i our your not no
ka ki ke ko se me mein hai hain tha thi aur ya par bhi ek
""".split())

MAX_TERMS = 8        # words per phrase
MAX_PHRASES = 150    # phrases per list
MAX_KEYWORD_LENGTH = 6000  # characters; keeps GET URLs under proxy limits

_QUOTED = re.compile(r'["“”]([^"“”]+)["“”]')
_ITEM_SEPARATORS = re.compile(r'[,;\n\r]+')
# Split on whitespace and punctuation only — never on \W, which would also
# split Indian-script words at their vowel signs.
_SEPARATORS = re.compile(r'[\s,;:!?()\[\]{}|/\\<>“”"‘’]+')
_EDGE_PUNCTUATION = ".'-_*#@&"
_POSSESSIVE = re.compile(r"'s$")


def normalize_text(text: str) -> str:
    """NFKC-normalize + casefold so Telugu/Hindi/English/other Indian-script
    text compares correctly regardless of composed/decomposed form or case."""
    if not text:
        return ''
    return unicodedata.normalize('NFKC', text).casefold()


def _is_latin(term: str) -> bool:
    return term[:1].isascii() and term[:1].isalnum()


_NON_WORD = re.compile(r'[^a-z0-9]+')


def ascii_words(text: str) -> str:
    """Normalized text as ' word word … ' — every run of non-[a-z0-9] becomes one
    space. Latin terms are matched against this, so "starts a word" is a plain
    substring test (' ' + term), and the same rule runs in Postgres as a
    tsvector prefix query (saga-police news.keywords.js)."""
    return f" {_NON_WORD.sub(' ', text).strip()} "


@lru_cache(maxsize=8192)
def _latin_needle(term: str) -> str:
    return ' ' + _NON_WORD.sub(' ', term).strip()


def contains_term(needle: str, haystack: str, words: Optional[str] = None) -> bool:
    """`needle` and `haystack` already normalized; `words` = ascii_words(haystack)
    when the caller has it (it's reused across many terms per article).

    Latin-script terms match from the start of a word: 'kill' finds
    'killed'/'killing' but not 'skill'; 'hit-and-run' finds 'hit and run'.
    Indian scripts keep plain substring matching — their vowel signs aren't
    word characters, so word boundaries would land inside words there."""
    if not needle:
        return False
    if _is_latin(needle):
        return _latin_needle(needle) in (words if words is not None else ascii_words(haystack))
    return needle in haystack


@dataclass
class Phrase:
    text: str          # normalized, as typed (for display and the phrase bonus)
    terms: List[str]   # words that must all appear for a list item to match


@dataclass
class KeywordQuery:
    phrases: List[Phrase] = field(default_factory=list)
    required: List[str] = field(default_factory=list)  # "quoted": every result must contain all
    terms: List[str] = field(default_factory=list)     # every distinct word across phrases, in order

    @property
    def is_list(self) -> bool:
        return len(self.phrases) > 1

    @property
    def empty(self) -> bool:
        return not self.phrases and not self.required


def _tokens(text: str) -> List[str]:
    out: List[str] = []
    for raw in _SEPARATORS.split(text):
        token = _POSSESSIVE.sub('', raw.strip(_EDGE_PUNCTUATION)).strip(_EDGE_PUNCTUATION)
        if token and token not in out:
            out.append(token)
    return out


def parse_keyword(keyword: Optional[str]) -> KeywordQuery:
    if not keyword or not keyword.strip():
        return KeywordQuery()
    text = normalize_text(keyword.strip())

    required = [p.strip() for p in _QUOTED.findall(text) if p.strip()]
    items = [i for i in _ITEM_SEPARATORS.split(_QUOTED.sub(' ', text)) if i.strip()]

    phrases: List[Phrase] = []
    seen_terms = set()
    for item in items:
        tokens = _tokens(item)
        terms = [t for t in tokens if len(t) > 1 and t not in STOP_WORDS]
        if not terms and len(items) == 1 and not required:
            terms = tokens  # a search for just "the": better than matching everything
        terms = terms[:MAX_TERMS]
        key = tuple(sorted(terms))
        if not terms or key in seen_terms:
            continue  # a list item of only common words, or a duplicate
        seen_terms.add(key)
        phrases.append(Phrase(text=' '.join(tokens), terms=terms))
        if len(phrases) == MAX_PHRASES:
            break
    # Computed once here: match_keyword runs per article, often hundreds per request.
    terms = list(dict.fromkeys(t for p in phrases for t in p.terms))
    return KeywordQuery(phrases=phrases, required=required, terms=terms)


@dataclass
class KeywordMatch:
    matched_terms: List[str]     # distinct words found (for highlighting)
    matched_phrases: List[str]   # list items fully matched (lists only)
    phrase: bool                 # one-phrase searches: the whole phrase appears as written
    title_hits: int              # matched words that are in the title
    is_list: bool

    @property
    def score(self) -> int:
        """Monotonic with sort_key. Lists: phrases, then words, then title hits.
        One phrase: words, then the whole phrase, then title hits."""
        title = min(self.title_hits, 9 if not self.is_list else 999)
        if self.is_list:
            return len(self.matched_phrases) * 10_000_000 + len(self.matched_terms) * 1000 + title
        return len(self.matched_terms) * 1000 + (100 if self.phrase else 0) + title * 10

    @property
    def sort_key(self):
        return (len(self.matched_phrases), len(self.matched_terms), self.phrase, self.title_hits)


def match_keyword(
    title: str, summary: str, content: str, query: KeywordQuery, min_match: int = 1,
) -> Optional[KeywordMatch]:
    """None when the article doesn't qualify: a quoted phrase is missing, or it
    matches fewer than `min_match` words (one phrase) / phrases (a list).
    An empty query matches everything with score 0."""
    if query.empty:
        return KeywordMatch([], [], False, 0, False)

    norm_title = normalize_text(title or '')
    haystack = f"{norm_title} {normalize_text(summary or '')} {normalize_text(content or '')}"
    words, title_words = ascii_words(haystack), ascii_words(norm_title)

    if not all(contains_term(p, haystack, words) for p in query.required):
        return None

    found = {t for t in query.terms if contains_term(t, haystack, words)}
    matched_terms = [t for t in query.terms if t in found]
    title_hits = sum(1 for t in matched_terms if contains_term(t, norm_title, title_words))
    need = max(1, min_match)

    if query.is_list:
        matched_phrases = [p.text for p in query.phrases if all(t in found for t in p.terms)]
        if len(matched_phrases) < need:
            return None
        return KeywordMatch(matched_terms, matched_phrases, False, title_hits, True)

    if query.phrases and len(matched_terms) < min(need, len(query.terms)):
        return None
    single = query.phrases[0] if query.phrases else None
    phrase = bool(single and len(single.terms) > 1 and contains_term(single.text, haystack, words))
    return KeywordMatch(matched_terms, [], phrase, title_hits, False)


def matches_keyword(title: str, summary: str, content: str, keyword: Optional[str] = None) -> bool:
    return match_keyword(title, summary, content, parse_keyword(keyword)) is not None
