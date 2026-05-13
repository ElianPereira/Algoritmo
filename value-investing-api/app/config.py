from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./value_investing.db"
    risk_free_rate: float = 0.045
    tax_rate_mex: float = 0.10
    commission_rate: float = 0.0025
    log_level: str = "INFO"
    cache_ttl_seconds: int = 86400

    # Telegram alerts
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    z_score_alert_threshold: float = 3.0
    f_score_alert_threshold: int = 7


settings = Settings()
