"""Run the API with: uvicorn apps.api.main:app --reload."""

from dev_assistant.api.main import app

__all__ = ["app"]
