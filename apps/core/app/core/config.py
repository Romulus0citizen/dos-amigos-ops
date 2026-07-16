from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "dos-amigos-core"
    app_env: str = "development"
    log_level: str = "INFO"
    api_port: int = 8000

    database_url: str = (
        "postgresql+psycopg://dos_amigos:change-me-locally@localhost:5432/dos_amigos"
    )

    iiko_adapter: str = "mock"
    iiko_base_url: str = ""
    iiko_username: str = ""
    iiko_password: str = ""
    iiko_organization_id: str = "8340002"
    iiko_timeout_seconds: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
