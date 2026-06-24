from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "LabFoundry"
    appliance_hostname: str = "labfoundry"
    environment: str = "development"
    database_url: str = "sqlite:///./data/labfoundry.db"
    secret_key: str = Field(default="change-me-in-production", min_length=16)
    secrets_key: str = ""
    session_cookie_name: str = "labfoundry_session"
    csrf_cookie_name: str = "labfoundry_csrf"
    jwt_issuer: str = "labfoundry"
    jwt_audience: str = "labfoundry-api"
    api_token_ttl_days: int = 90
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "labfoundry-admin"
    dry_run_system_adapters: bool = True
    repository_path: Path = Path("/mnt/labfoundry-vcf-offline-depot")
    vcf_backup_path: Path = Path("/mnt/labfoundry-vcf-backups")

    model_config = SettingsConfigDict(
        env_prefix="LABFOUNDRY_",
        env_file=".env",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
