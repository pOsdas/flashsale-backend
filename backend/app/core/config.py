"""Pydantic settings here"""
from functools import lru_cache
from pydantic import AnyUrl, Field
from typing import List
from pydantic_settings import SettingsConfigDict, BaseSettings


class RunModel(BaseSettings):
    host: str = "127.0.0.1"
    port: int = 8010


class ApiV1Prefix(BaseSettings):
    prefix: str = "/v1"


class ApiPrefix(BaseSettings):
    prefix: str = "/api"
    v1: ApiV1Prefix = ApiV1Prefix()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env-template", ".env"),
        case_sensitive=False,
        extra="ignore",
    )
    # ENV
    env: str = Field(default="dev", description="dev|stage|prod")

    # POSTGRES
    postgres_db: str
    postgres_user: str
    postgres_password: str
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # BACKEND
    debug: bool = False
    secret_key: str
    allowed_hosts: List[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1"])

    # CELERY
    celery_broker_url: AnyUrl
    celery_result_backend: AnyUrl | None = None

    # REDIS
    redis_url: AnyUrl

    run: RunModel = RunModel()
    api: ApiPrefix = ApiPrefix()

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
