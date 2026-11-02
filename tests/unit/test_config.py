from pathlib import Path

import pytest
from pydantic import ValidationError

from dev_assistant.core.config import Settings


def test_settings_have_safe_development_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment == "development"
    assert settings.log_level == "INFO"
    assert settings.embedding_cache_directory == Path("data/huggingface")


def test_settings_reject_unknown_environment() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production-ish", _env_file=None)


def test_settings_bound_generated_answer_length() -> None:
    settings = Settings(_env_file=None)

    assert settings.llm_max_output_tokens == 1200
    with pytest.raises(ValidationError):
        Settings(llm_max_output_tokens=0, _env_file=None)


def test_production_requires_api_and_demo_credentials() -> None:
    with pytest.raises(ValidationError, match="Production requires"):
        Settings(environment="production", _env_file=None)

    settings = Settings(
        environment="production",
        api_access_token="server-token",
        demo_access_password="demo-password",
        _env_file=None,
    )
    assert settings.api_access_token.get_secret_value() == "server-token"
