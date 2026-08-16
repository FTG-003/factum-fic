"""Configurazione ibrida: env + YAML con override tipizzato."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurazione letta da .env (priority) + YAML opzionale."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Factum Parse API
    factum_api_url: str = Field(
        default="https://api.factum.pyragogy.org",
        alias="FACTUM_API_URL",
    )
    factum_api_key: str = Field(default="", alias="FACTUM_API_KEY")

    # Fatture in Cloud v2
    fic_base_url: str = Field(
        default="https://api-v2.fattureincloud.it",
        alias="FIC_BASE_URL",
    )
    fic_api_key: str = Field(default="", alias="FIC_API_KEY")
    fic_company_id: str = Field(default="", alias="FIC_COMPANY_ID")

    # Directory gestione fatture (italiano)
    inbox_dir: str = Field(default="./da_elaborare", alias="INBOX_DIR")
    processed_dir: str = Field(default="./elaborate", alias="PROCESSED_DIR")
    failed_dir: str = Field(default="./errori", alias="FAILED_DIR")

    # Watcher
    watch_dir: str = Field(default="~/Downloads", alias="WATCH_DIR")

    # Config file YAML (categorie, conti)
    config_file: Path | None = Field(default=None, alias="CONFIG_FILE")


def load_settings() -> Settings:
    """Carica settings da .env e YAML opzionale."""
    return Settings()  # type: ignore[call-arg]


def load_yaml_config(path: Path | None) -> dict[str, Any]:
    """Carica e validazione YAML categorie/conti.

    Restituisce un dict vuoto se il file non esiste.
    """
    if path is None or not path.exists():
        return {}
    import yaml

    with open(path) as f:
        return yaml.safe_load(f) or {}
