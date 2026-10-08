"""Service settings. Secrets live only in .env (PD-8); .env.example carries placeholders."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://cryptoos:@localhost:5432/cryptoos"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    next_public_api_base: str = "http://127.0.0.1:8000"
    # Comma-separated browser origins allowed to call this API cross-origin
    # (CORS): spec 3.1 runs the web on 3000 and the API on 8000, so the two are
    # different origins and the browser refuses the calls unless listed here.
    allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # Storage root for bars (PD-7: immutable, gitignored). The budget that caps
    # it lives in config/defaults.yaml[data.storage_budget_gb] (PD-11, D-006).
    data_dir: str = "data"

    @property
    def allowed_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


def get_settings() -> Settings:
    return Settings()
