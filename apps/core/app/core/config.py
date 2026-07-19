import json
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from integrations.iiko.auth import AuthConfiguration
from integrations.iiko.client import IikoClient, build_iiko_client
from integrations.iiko.schemas import AuthKind, IikoMode


class Settings(BaseSettings):
    app_name: str = "dos-amigos-core"
    app_env: str = "development"
    log_level: str = "INFO"
    api_port: int = 8000
    business_timezone: str = "Etc/UTC"

    database_url: str = (
        "postgresql+psycopg://dos_amigos:change-me-locally@localhost:5432/dos_amigos"
    )

    iiko_mode: str = "mock"
    iiko_auth_type: str = "unknown"
    iiko_base_url: str = ""
    iiko_username: str = ""
    iiko_password: str = ""
    iiko_api_login: str = ""
    iiko_organization_id: str = "8340002"
    iiko_verify_tls: bool = True
    iiko_connect_timeout_seconds: int = 10
    iiko_read_timeout_seconds: int = 30
    iiko_max_retries: int = 3
    iiko_payment_category_map_json: str = "{}"
    sales_automation_enabled: bool = False
    sales_daily_run_local_time: str = "06:00"
    sales_backfill_max_days: int = 14
    sales_retry_max_attempts: int = 4
    sales_retry_base_seconds: int = 30
    sales_outbox_enabled: bool = True
    hermes_delivery_mode: str = "disabled"
    report_outbox_internal_token: str = ""

    def iiko_auth_configuration(self) -> AuthConfiguration:
        return AuthConfiguration(
            kind=AuthKind(self.iiko_auth_type),
            username=self.iiko_username or None,
            password=self.iiko_password or None,
            api_login=self.iiko_api_login or None,
        )

    def build_iiko_client(self) -> IikoClient:
        return build_iiko_client(
            mode=IikoMode(self.iiko_mode),
            organization_ref=self.iiko_organization_id or None,
            base_url=self.iiko_base_url,
            auth_configuration=self.iiko_auth_configuration(),
            verify_tls=self.iiko_verify_tls,
            connect_timeout_seconds=self.iiko_connect_timeout_seconds,
            read_timeout_seconds=self.iiko_read_timeout_seconds,
            max_retries=self.iiko_max_retries,
        )

    def iiko_payment_category_map(self) -> dict[str, str]:
        parsed = json.loads(self.iiko_payment_category_map_json or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("IIKO_PAYMENT_CATEGORY_MAP_JSON must be a JSON object")
        return {str(key): str(value) for key, value in parsed.items()}

    @field_validator("sales_daily_run_local_time")
    @classmethod
    def validate_sales_daily_run_local_time(cls, value: str) -> str:
        parts = value.split(":")
        if len(parts) != 2 or not all(part.isdigit() and len(part) == 2 for part in parts):
            raise ValueError("SALES_DAILY_RUN_LOCAL_TIME must use HH:MM format")
        hour = int(parts[0])
        minute = int(parts[1])
        if hour > 23 or minute > 59:
            raise ValueError("SALES_DAILY_RUN_LOCAL_TIME must use HH:MM format")
        return value

    @field_validator("hermes_delivery_mode")
    @classmethod
    def validate_hermes_delivery_mode(cls, value: str) -> str:
        if value not in {"disabled", "mock"}:
            raise ValueError("HERMES_DELIVERY_MODE must be disabled or mock")
        return value

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
