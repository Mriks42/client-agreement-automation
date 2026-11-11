"""Application settings, loaded from environment variables (and backend/.env in dev)."""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_SECRET = "dev-insecure-change-me"  # noqa: S105 - placeholder, rejected in prod


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "test", "prod"] = "dev"
    # On Render these fall back to the platform-provided variables automatically.
    app_base_url: str = Field(
        "http://localhost:5173",
        validation_alias=AliasChoices("APP_BASE_URL", "RENDER_EXTERNAL_URL"),
    )
    git_sha: str = Field("dev", validation_alias=AliasChoices("GIT_SHA", "RENDER_GIT_COMMIT"))

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "agreements"

    session_secret: SecretStr = SecretStr(_INSECURE_SECRET)

    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-opus-5-5"
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"

    email_backend: Literal["console", "gmail_api"] = "console"
    email_product_name: str = "Kickoff"
    gmail_client_id: str | None = None
    gmail_client_secret: SecretStr | None = None
    gmail_refresh_token: SecretStr | None = None
    gmail_sender: str | None = None

    sentry_dsn: str | None = None
    run_worker: bool = True

    max_upload_mb: int = 20
    max_pages: int = 60
    libreoffice_bin: str = "soffice"

    # Enables /api/ops/* diagnostics when set. Never expose without it.
    ops_token: SecretStr | None = None

    frontend_dist: str = "../frontend/dist"

    @field_validator(
        "anthropic_api_key",
        "gmail_client_id",
        "gmail_client_secret",
        "gmail_refresh_token",
        "gmail_sender",
        "sentry_dsn",
        "ops_token",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # Hosting dashboards often store "not set" as an empty string.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _validate_prod(self) -> "Settings":
        if self.app_env != "prod":
            return self
        problems: list[str] = []
        if self.session_secret.get_secret_value() == _INSECURE_SECRET:
            problems.append("SESSION_SECRET must be set in prod")
        if self.email_backend == "gmail_api" and not all(
            [
                self.gmail_client_id,
                self.gmail_client_secret,
                self.gmail_refresh_token,
                self.gmail_sender,
            ]
        ):
            problems.append("GMAIL_* settings are required when EMAIL_BACKEND=gmail_api")
        if not self.app_base_url.startswith("https://"):
            problems.append("APP_BASE_URL must be https in prod")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
