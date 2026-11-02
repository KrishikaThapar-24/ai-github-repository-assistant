"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dev_assistant.api.routes import router
from dev_assistant.core.config import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")
extension_origins = (
    [f"chrome-extension://{settings.chrome_extension_id}"] if settings.chrome_extension_id else []
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501", *extension_origins],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)
app.include_router(router)


@app.get("/health", tags=["operations"])
def health_check() -> dict[str, str]:
    """Report that the API process is responding."""

    return {"status": "ok"}
