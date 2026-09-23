from fastapi import APIRouter

from api.schemas import HealthResponse

router = APIRouter(tags=['Health'])


@router.get('/health', response_model=HealthResponse, summary='Health check')
def health() -> dict:
    """Liveness probe. Returns `{"status": "ok"}` when the API is up."""
    return {'status': 'ok'}
