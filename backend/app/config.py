from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://poker:poker@localhost:5432/poker"
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_exp_hours: int = 24 * 7

    action_timeout_s: int = 15
    between_hands_s: float = 3.0
    bot_think_min_s: float = 0.8
    bot_think_max_s: float = 2.2
    runout_stage_s: float = 1.2
    room_lifetime_s: int = 60 * 60 * 2  # rooms auto-close 2 hours after creation


@lru_cache
def get_settings() -> Settings:
    return Settings()
