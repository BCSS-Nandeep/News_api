"""
The source registry endpoint. SocEye reads this to build its checkbox lists
(Country / Language / State / Websites) — the values are derived from
News_URLs.json here, never hardcoded in the frontend.
"""

from typing import List, Optional

from fastapi import APIRouter, Query

from api.schemas import SourceOut
from scraper.sources_registry import list_active_sources

router = APIRouter()

_MULTI = 'Comma-separated for multi-select; values are ORed together.'


@router.get('/news/sources', response_model=List[SourceOut])
def get_sources(
    country: Optional[str] = Query(None, description=f"e.g. 'India,United States'. {_MULTI}"),
    state: Optional[str] = Query(None, description=f"e.g. 'Telangana,Tamil Nadu'. {_MULTI}"),
    language: Optional[str] = Query(None, description=f"e.g. 'English,Telugu'. {_MULTI}"),
    source: Optional[str] = Query(None, description=f"Registry ids or name substrings, e.g. 'cnn,bbc_news'. {_MULTI}"),
) -> list:
    """Active sources, ANDed across filters and ORed within each one."""
    sources = list_active_sources(
        country=country, state=state, language=language, source_id=source,
    )
    return [s.__dict__ for s in sources]
