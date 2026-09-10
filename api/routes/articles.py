"""
The single primary article endpoint (GET /news/articles) plus a
by-id lookup. No separate /search, /location, /state, /language or /country
sub-routes — every filter is an optional query param on one endpoint.

Each filter is multi-value for checkbox-style frontends: pass comma-separated
values (?country=India,United States). Values inside one filter are ORed,
different filters are ANDed:

    ?country=India,United States&language=English,Telugu&state=Telangana
      -> (India OR United States) AND (English OR Telugu) AND (Telangana)
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.schemas import ArticleListResponse, ArticleOut
from services import news_service

router = APIRouter()

_MULTI = 'Comma-separated for multi-select; values are ORed together.'


@router.get('/news/articles', response_model=ArticleListResponse)
def get_articles(
    keyword: Optional[str] = Query(None, description="e.g. 'drugs', 'corruption', 'accident'"),
    country: Optional[str] = Query(None, description=f"e.g. 'India,United States'. {_MULTI}"),
    language: Optional[str] = Query(None, description=f"e.g. 'English,Telugu'. {_MULTI}"),
    location: Optional[str] = Query(None, description=f"Matched against district/location/state. {_MULTI}"),
    state: Optional[str] = Query(None, description=f"e.g. 'Telangana,Andhra Pradesh'. {_MULTI}"),
    district: Optional[str] = Query(None, description=f"e.g. 'Hyderabad,Karimnagar'. {_MULTI}"),
    source: Optional[str] = Query(None, description=f"Registry ids (preferred) or name substrings, e.g. 'cnn,bbc_news'. {_MULTI}"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict:
    return news_service.get_articles(
        keyword=keyword,
        country=country,
        language=language,
        location=location,
        state=state,
        district=district,
        source=source,
        limit=limit,
        offset=offset,
    )


@router.get('/news/articles/{article_id}', response_model=ArticleOut)
def get_article(article_id: str) -> dict:
    """Only finds articles currently in the warm in-memory cache (no
    persistent DB backs this API) — request /news/articles with matching
    filters first so the source is scraped and cached, then look up its id."""
    article = news_service.get_article_by_id(article_id)
    if not article:
        raise HTTPException(status_code=404, detail='Article not found (not in cache — it may have expired or never been scraped)')
    return article
