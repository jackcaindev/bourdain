"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Raised when required runtime configuration is missing or invalid."""


class Settings(BaseSettings):
    """Runtime settings for The Bourdain Brief backend."""

    anthropic_api_key: SecretStr = Field(
        description="Anthropic API key used for Claude model calls."
    )
    anthropic_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Timeout, in seconds, for Anthropic API requests.",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


_ENV_VAR_NAMES = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "anthropic_timeout_seconds": "ANTHROPIC_TIMEOUT_SECONDS",
}


@lru_cache
def get_settings() -> Settings:
    """Return cached settings, failing fast with clear missing-key details."""

    try:
        return Settings()
    except ValidationError as exc:
        missing = [
            _ENV_VAR_NAMES.get(str(error["loc"][0]), str(error["loc"][0]))
            for error in exc.errors()
            if error["type"] == "missing"
        ]
        if missing:
            raise ConfigurationError(
                "Missing required environment variable(s): "
                f"{', '.join(sorted(set(missing)))}"
            ) from exc

        raise ConfigurationError(f"Invalid application configuration: {exc}") from exc
