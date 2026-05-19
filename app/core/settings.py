"""
Core application settings — loaded from environment variables or .env file.
All conversion defaults are centralised here for easy tuning.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ────────────────────────────────────────────────────────
    app_name: str = "Redshift → Fabric DDL Converter"
    app_version: str = "1.0.0"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ── API ─────────────────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = ["*"]

    # ── Storage ─────────────────────────────────────────────────────────────
    upload_dir: Path = Path("data/uploads")
    output_dir: Path = Path("data/outputs")
    reports_dir: Path = Path("data/reports")
    logs_dir: Path = Path("data/logs")
    max_upload_size_mb: int = 50

    # ── Conversion ──────────────────────────────────────────────────────────
    # Fabric parameterisation placeholders (match reference repo conventions)
    placeholder_output_schema: str = "${os_bi_alefdw}"      # target schema for views/procs
    placeholder_read_schema: str = "${rs_bi_alefdw}"         # read schema for bi_alefdw tables
    placeholder_alefdw_schema: str = "${rs_alefdw}"          # source alefdw DW tables
    placeholder_schema: str = "${schema}"                    # generic Flyway schema placeholder
    placeholder_database: str = "${database}"                # database placeholder

    # Strip these suffixes from Redshift object names when converting to Fabric
    strip_name_suffixes: list[str] = ["_mv", "_view"]

    # Whether to strip _view / _mv suffixes from table references in views
    strip_table_suffixes_in_views: bool = True

    # Source schema prefixes → Fabric placeholder mapping
    # Keys are lowercased source schema names
    schema_placeholder_map: dict[str, str] = {
        "bi_alefdw":     "${rs_bi_alefdw}",
        "bi_alefdw_dev": "${rs_bi_alefdw}",
        "alefdw":        "${rs_alefdw}",
        "alefdw_dev":    "${rs_alefdw}",
    }

    # Output view/proc schema placeholder
    output_schema_placeholder: str = "${os_bi_alefdw}"

    # Flyway migration schema placeholder (used in table DDL)
    flyway_schema_placeholder: str = "${schema}"

    # ── Rule engine ─────────────────────────────────────────────────────────
    rules_config_path: Path = Path("config/rules.yaml")

    # ── Confidence thresholds ───────────────────────────────────────────────
    confidence_high_threshold: float = 0.90
    confidence_partial_threshold: float = 0.65


settings = Settings()


def ensure_directories() -> None:
    """Create all required data directories at startup."""
    for d in (
        settings.upload_dir,
        settings.output_dir,
        settings.reports_dir,
        settings.logs_dir,
    ):
        d.mkdir(parents=True, exist_ok=True)
