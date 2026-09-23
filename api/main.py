"""
Blura News API — FastAPI app for SocEye.

    DISCOVER -> SCRAPE -> NORMALIZE -> FILTER -> RETURN ARTICLES

Run with: python main.py   (or: uvicorn api.main:app --reload)
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from api.routes import articles, health, sources

_STATIC_DIR = Path(__file__).resolve().parent.parent / 'static'
_UI_FILE = _STATIC_DIR / 'index.html'
_REFERENCE_FILE = _STATIC_DIR / 'reference.html'

app = FastAPI(
    title='Blura News API',
    description=(
        'Discovers, scrapes and normalizes Indian and international news articles for SocEye.\n\n'
        'Multi-select filters take comma-separated values: values within one filter are ORed, '
        'different filters are ANDed.'
    ),
    version='1.0.0',
    openapi_tags=[
        {'name': 'Health', 'description': 'Service liveness.'},
        {'name': 'Sources', 'description': 'The news source registry (built from News_URLs.json).'},
        {'name': 'Articles', 'description': 'Scraped, normalized articles with keyword/location/language filters.'},
    ],
)

app.include_router(health.router)
app.include_router(sources.router)
app.include_router(articles.router)


@app.get('/', include_in_schema=False)
def ui() -> FileResponse:
    """Browser dashboard. Served from the API itself rather than opened as a
    local file so it shares an origin with the endpoints it calls — a file://
    page would be blocked by CORS on every request."""
    return FileResponse(_UI_FILE)


@app.get('/reference', include_in_schema=False)
def api_reference() -> FileResponse:
    """Interactive API docs + request playground (Scalar), rendered from
    /openapi.json. Swagger UI remains at /docs."""
    return FileResponse(_REFERENCE_FILE)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Per-source scraping failures are already caught inside
    services/news_service.py so one dead source never reaches here. This is
    the last-resort net for anything unexpected, so a bug never surfaces to
    SocEye as a raw connection failure instead of a proper HTTP response."""
    return JSONResponse(status_code=500, content={'detail': f'{type(exc).__name__}: {exc}'})
