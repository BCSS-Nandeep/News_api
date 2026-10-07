"""
Pydantic response models for the News API. Deliberately article-only, no
sentiment/political-scoring fields anywhere.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ArticleOut(BaseModel):
    id: str
    title: str
    content: str
    summary: str
    source: str
    source_id: str
    source_url: str
    language: str
    country: str
    state: str
    district: str
    location: str
    published_at: datetime
    image_url: str
    matched_terms: List[str] = Field(
        default_factory=list,
        description='Which of the query_terms this article contains (keyword searches only).',
    )
    matched_phrases: List[str] = Field(
        default_factory=list,
        description='Keyword lists only: which of the query_phrases this article fully contains.',
    )
    match_score: int = Field(
        0,
        description='Keyword relevance used for ordering (higher first). Lists: phrases matched, then '
                    'words, then title hits. One phrase: words matched, then the whole phrase, then '
                    'title hits. 0 without a keyword.',
    )


class ArticleListResponse(BaseModel):
    count: int
    limit: int
    offset: int
    articles: List[ArticleOut]
    pending_sources: int = Field(
        0,
        description=(
            'Matching sources still being scraped when the response was sent. '
            'They finish in the background; repeat the request shortly to include them.'
        ),
    )
    query_terms: List[str] = Field(
        default_factory=list,
        description='Every distinct word searched for (common words dropped; "quoted phrases" '
                    'are required and not listed).',
    )
    query_phrases: List[str] = Field(
        default_factory=list,
        description='The keyword list as parsed (duplicates and common-word-only items dropped). '
                    'Empty for a single phrase.',
    )


class SourceOut(BaseModel):
    id: str
    name: str
    region: str
    country: str
    state: str
    language: str
    type: str
    base_url: str
    active: bool


class HealthResponse(BaseModel):
    status: str
