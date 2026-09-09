"""
Pydantic response models for the News API. Deliberately article-only, no
sentiment/political-scoring fields anywhere.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class ArticleOut(BaseModel):
    id: str
    title: str
    content: str
    summary: str
    source: str
    source_id: str
    source_url: str
    language: str
    state: str
    district: str
    location: str
    published_at: datetime
    image_url: str


class ArticleListResponse(BaseModel):
    count: int
    limit: int
    offset: int
    articles: List[ArticleOut]


class SourceOut(BaseModel):
    id: str
    name: str
    region: str
    state: str
    language: str
    type: str
    base_url: str
    active: bool


class HealthResponse(BaseModel):
    status: str
