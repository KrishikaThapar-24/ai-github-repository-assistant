"""Validated application settings loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration; secrets are intentionally not given defaults."""

    model_config = SettingsConfigDict(
        env_prefix="DEV_ASSISTANT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI GitHub Repository Agent"
    environment: str = Field(default="development", pattern="^(development|test|production)$")
    log_level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    llm_provider: Literal["openai", "anthropic"] = "openai"
    indexing_mode: Literal["background", "inline"] = "background"
    llm_model: str | None = None
    llm_timeout_seconds: int = Field(default=60, ge=1, le=300)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_max_output_tokens: int = Field(default=1200, ge=128, le=4096)
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    api_access_token: SecretStr | None = None
    demo_access_password: SecretStr | None = None
    github_token: SecretStr | None = None
    github_api_url: str = "https://api.github.com"
    github_activity_backend: Literal["rest", "mcp"] = "rest"
    github_mcp_command: str = "docker"
    github_mcp_image: str = "ghcr.io/github/github-mcp-server:latest"
    github_mcp_timeout_seconds: int = Field(default=120, ge=10, le=600)
    chrome_extension_id: str | None = Field(default=None, pattern=r"^[a-p]{32}$")
    github_timeout_seconds: int = Field(default=30, ge=1, le=180)
    github_max_archive_bytes: int = Field(default=64 * 1024 * 1024, ge=1_000_000)
    github_max_pull_request_diff_bytes: int = Field(default=2 * 1024 * 1024, ge=16_384)
    embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_cache_directory: Path = Path("data/huggingface")
    chroma_persist_directory: Path = Path("data/chroma")
    lexical_database_path: Path = Path("data/code-search.sqlite3")
    dependency_database_path: Path = Path("data/dependencies.sqlite3")

    @model_validator(mode="after")
    def require_demo_credentials_in_production(self) -> "Settings":
        if self.environment == "production":
            if self.api_access_token is None or self.demo_access_password is None:
                raise ValueError(
                    "Production requires API access token and demo access password."
                )
            provider_key = (
                self.openai_api_key
                if self.llm_provider == "openai"
                else self.anthropic_api_key
            )
            if provider_key is None:
                raise ValueError(f"Production requires an API key for {self.llm_provider}.")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a cached settings object for this process."""

    return Settings()
