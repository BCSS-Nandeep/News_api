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
        description='Words of the keywords this article contains (for highlighting).',
    )
    matched_phrases: List[str] = Field(
        default_factory=list,
        description='Which of the query_phrases (keywords) this article contains.',
    )
    match_score: int = Field(
        0,
        description='Keyword relevance used for ordering (higher first): keywords matched, then '
                    'keywords found in the title. 0 without a keyword.',
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
        description='Every distinct word across the keywords.',
    )
    query_phrases: List[str] = Field(
        default_factory=list,
        description='The keywords as parsed (comma-separated, duplicates dropped), each matched '
                    'as a whole phrase.',
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
