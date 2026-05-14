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

    # WhatsApp alerts via Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_whatsapp: str = ""   # e.g. whatsapp:+14155238886
    twilio_to_whatsapp: str = "whatsapp:+529992689400"
    z_score_alert_threshold: float = 3.0
    f_score_alert_threshold: int = 7


settings = Settings()
