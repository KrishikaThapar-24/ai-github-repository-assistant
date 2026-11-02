import pytest
from pydantic import ValidationError

from dev_assistant.core.config import Settings


def test_settings_have_safe_development_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.environment == "development"
    assert settings.log_level == "INFO"


def test_settings_reject_unknown_environment() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production-ish", _env_file=None)
