from typing import List, Optional

from fastapi import APIRouter, Query

from api.schemas import SourceOut
from scraper.sources_registry import list_active_sources

router = APIRouter()


@router.get('/news/sources', response_model=List[SourceOut])
def get_sources(
    state: Optional[str] = Query(None, description="Substring match, e.g. 'Telangana'"),
    language: Optional[str] = Query(None, description="Substring match, e.g. 'Telugu'"),
) -> list:
    sources = list_active_sources(state=state, language=language)
    return [s.__dict__ for s in sources]
