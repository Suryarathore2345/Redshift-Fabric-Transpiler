"""
Core application settings — loaded from environment variables or .env file.

Schema Placeholder Auto-Generation
────────────────────────────────────
Any schema name NOT listed in schema_placeholder_map_overrides is
automatically mapped at runtime using the naming convention:

    source schema  → ${rs_<schema_name>}    (read schema in view bodies)
    output schema  → ${os_<schema_name>}    (schema of the view itself)

Examples (auto-generated, no config needed):
    sales     → ${rs_sales}
    master    → ${rs_master}
    reporting → ${os_reporting}   (when it appears as the view's own schema)

Override specific schemas in schema_placeholder_map_overrides when:
  - dev and prod schemas should share the same placeholder
  - you want a custom placeholder name instead of the auto-generated one

You NEVER need to touch this file just because a new schema appears.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

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
    # Flyway migration schema placeholder (used in CREATE TABLE)
    flyway_schema_placeholder: str = "${schema}"

    # Strip these suffixes from Redshift object names when converting to Fabric
    strip_name_suffixes: list[str] = ["_mv", "_view"]

    # Whether to strip _view / _mv suffixes from table references in views
    strip_table_suffixes_in_views: bool = True

    # ── Schema placeholder OVERRIDES ─────────────────────────────────────────
    #
    # Only list schemas here when you need NON-DEFAULT behaviour:
    #   • Dev/prod aliases sharing one placeholder
    #   • Custom placeholder names
    #
    # For every OTHER schema the converter encounters, placeholders are
    # auto-generated:   <schema>  →  ${rs_<schema>}
    #
    # Examples of what you'd put here:
    #   "bi_alefdw_dev": "${rs_bi_alefdw}"   ← dev maps to same as prod
    #   "alefdw_dev":    "${rs_alefdw}"       ← dev maps to same as prod
    #
    schema_placeholder_map_overrides: dict[str, str] = {
        # ── Schemas where dev == prod placeholder ──────────────────────────
        "bi_alefdw_dev": "${rs_bi_alefdw}",
        "alefdw_dev":    "${rs_alefdw}",
    }

    # ── Rule engine ─────────────────────────────────────────────────────────
    rules_config_path: Path = Path("config/rules.yaml")

    # ── Confidence thresholds ───────────────────────────────────────────────
    confidence_high_threshold: float = 0.90
    confidence_partial_threshold: float = 0.65

    # ── Placeholder naming conventions ───────────────────────────────────────
    # Prefix applied to auto-generated READ schema placeholders
    rs_prefix: str = "rs"
    # Prefix applied to auto-generated OUTPUT schema placeholders
    os_prefix: str = "os"

    # ── Runtime computed properties ───────────────────────────────────────────
    # (populated on first call, NOT stored in settings)

    def get_read_placeholder(self, schema: str) -> str:
        """
        Return the Fabric placeholder for a source schema found in a view body.

        Resolution order:
          1. Check schema_placeholder_map_overrides (exact, case-insensitive)
          2. Auto-generate:  ${rs_<schema>}

        Examples:
          "bi_alefdw"     → "${rs_bi_alefdw}"   (from auto-gen)
          "bi_alefdw_dev" → "${rs_bi_alefdw}"   (from override)
          "sales"         → "${rs_sales}"        (auto-gen — no config needed)
          "master"        → "${rs_master}"       (auto-gen)
          "MY_SCHEMA"     → "${rs_my_schema}"   (auto-gen, lowercased + sanitised)
        """
        key = schema.lower().strip()
        # Check overrides first
        if key in self.schema_placeholder_map_overrides:
            return self.schema_placeholder_map_overrides[key]
        # Auto-generate: sanitise schema name (replace non-word chars with _)
        safe_name = re.sub(r"[^a-z0-9]", "_", key)
        return "${" + self.rs_prefix + "_" + safe_name + "}"

    def get_output_placeholder(self, schema: str) -> str:
        """
        Return the Fabric placeholder for the output schema
        (the schema that owns the VIEW / PROCEDURE being created).

        Always auto-generated as ${os_<schema>} — no overrides needed
        since each project's output schema is distinct.

        Examples:
          "bi_alefdw"  → "${os_bi_alefdw}"
          "reporting"  → "${os_reporting}"
          "sales_dw"   → "${os_sales_dw}"
        """
        safe_name = re.sub(r"[^a-z0-9]", "_", schema.lower().strip())
        return "${" + self.os_prefix + "_" + safe_name + "}"

    # Legacy compatibility — kept so existing code that reads these directly
    # still works. Both now delegate to the runtime methods above.
    @property
    def schema_placeholder_map(self) -> dict[str, str]:
        """
        Full combined map including overrides.
        Used only by legacy callers; prefer get_read_placeholder() for new code.
        """
        return self.schema_placeholder_map_overrides

    @property
    def output_schema_placeholder(self) -> str:
        """Legacy compat — returns the default output placeholder."""
        return "${os_bi_alefdw}"


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
