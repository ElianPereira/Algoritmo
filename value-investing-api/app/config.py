from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./value_investing.db"
    risk_free_rate: float = 0.045
    tax_rate_mex: float = 0.10
    commission_rate: float = 0.0025
    log_level: str = "INFO"
    cache_ttl_seconds: int = 86400


settings = Settings()
