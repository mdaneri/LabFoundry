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
    appliance_fqdn: str = "labfoundry.labfoundry.internal"
    appliance_management_cidr: str = "192.168.49.1/24"
    appliance_external_dns_servers: str = "1.1.1.1\n9.9.9.9"
    appliance_ntp_servers: str = "time.cloudflare.com\nnts.netnod.se"
    dry_run_system_adapters: bool = True
    management_source_cidr: str = "192.168.49.0/24"
    repository_path: Path = Path("/mnt/labfoundry-vcf-offline-depot")
    vcf_backup_path: Path = Path("/mnt/labfoundry-vcf-backups")
    app_log_path: Path = Path("/var/log/labfoundry/labfoundry.log")
    esxi_kickstart_max_bytes: int = 262_144
    esxi_installer_iso_max_bytes: int = 1024 * 1024 * 1024
    monitor_enabled: bool = True
    monitor_sample_interval_seconds: int = 30
    monitor_retention_hours: int = 6

    model_config = SettingsConfigDict(
        env_prefix="LABFOUNDRY_",
        env_file=".env",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
