"""FastAPI application entry point."""

import hmac

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from dev_assistant.api.routes import router
from dev_assistant.core.config import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")
extension_origins = (
    [f"chrome-extension://{settings.chrome_extension_id}"] if settings.chrome_extension_id else []
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", 
                   "http://127.0.0.1:8501",
                   "https://ai-app-repository-assistant-6t377znnmrbbi4bbfa9lvw.streamlit.app/", *extension_origins],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(router)


@app.middleware("http")
async def require_api_access_token(request, call_next):
    """Protect API routes when a deployment configures a shared server-side token."""

    token = settings.api_access_token
    if request.url.path.startswith("/api/v1/") and request.method != "OPTIONS" and token:
        scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
        expected = token.get_secret_value()
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            supplied.encode("utf-8"), expected.encode("utf-8")
        ):
            return JSONResponse(
                status_code=401,
                content={"detail": "A valid bearer token is required."},
                headers={"WWW-Authenticate": "Bearer"},
            )
    return await call_next(request)


@app.get("/health", tags=["operations"])
def health_check() -> dict[str, str]:
    """Report that the API process is responding."""

    return {"status": "ok"}
