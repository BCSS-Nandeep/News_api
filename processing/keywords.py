"""
Generic, caller-supplied keyword matching for the News API.

The keyword comes from the request — "drugs", "narcotics", "corruption",
anything — rather than a fixed server-side topic list, so SocEye decides what
it's looking for. No default keyword requirement: an article always matches
when no `keyword` filter is supplied.
"""

import unicodedata
from typing import Optional


def normalize_text(text: str) -> str:
    """NFKC-normalize + casefold so Telugu/Hindi/English/other Indian-script
    text compares correctly regardless of composed/decomposed form or case."""
    if not text:
        return ''
    return unicodedata.normalize('NFKC', text).casefold()


def matches_keyword(title: str, summary: str, content: str, keyword: Optional[str] = None) -> bool:
    if not keyword or not keyword.strip():
        return True
    needle = normalize_text(keyword.strip())
    haystack = normalize_text(f"{title or ''} {summary or ''} {content or ''}")
    return needle in haystack
