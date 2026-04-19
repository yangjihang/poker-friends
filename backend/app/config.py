from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://poker:poker@localhost:5432/poker"
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_exp_hours: int = 24 * 7

    # 逗号分隔；"*" 表示放通（dev 用）。生产填域名，如 "https://poker.example.com"
    cors_origins: str = "*"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    action_timeout_s: int = 15
    between_hands_s: float = 3.0
    bot_think_min_s: float = 0.8
    bot_think_max_s: float = 2.2
    runout_stage_s: float = 1.2
    room_lifetime_s: int = 60 * 60 * 2  # rooms auto-close 2 hours after creation

    # 新用户注册奖励
    register_bonus: int = 20000

    # admin 账户 bootstrap：都填了才会在启动时自动创建该 admin。
    admin_username: str | None = None
    admin_password: str | None = None
    admin_display_name: str = "Admin"

    # /metrics 访问令牌：留空则 /metrics 公开（仅建议 dev）；填了需 Bearer 匹配
    metrics_token: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
