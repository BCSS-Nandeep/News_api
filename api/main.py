"""
Blura News API — FastAPI app for SocEye.

    DISCOVER -> SCRAPE -> NORMALIZE -> FILTER -> RETURN ARTICLES

Run with: python main.py   (or: uvicorn api.main:app --reload)
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from api.routes import articles, health, sources

_UI_FILE = Path(__file__).resolve().parent.parent / 'static' / 'index.html'

app = FastAPI(
    title='Blura News API',
    description='Discovers, scrapes and normalizes Indian and international news articles for SocEye.',
    version='1.0.0',
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


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Per-source scraping failures are already caught inside
    services/news_service.py so one dead source never reaches here. This is
    the last-resort net for anything unexpected, so a bug never surfaces to
    SocEye as a raw connection failure instead of a proper HTTP response."""
    return JSONResponse(status_code=500, content={'detail': f'{type(exc).__name__}: {exc}'})
