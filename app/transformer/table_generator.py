"""
Table DDL Generator

Takes a TableIR object and emits production-ready Fabric T-SQL:

  IF OBJECT_ID('${schema}.table_name', 'U') IS NULL
  BEGIN
      CREATE TABLE ${schema}.table_name (
          col1  TYPE1,
          col2  TYPE2,
          ...
      );
  END;

Patterns derived from reference repo (V3__Create_Table.sql, V7__Create_Tables.sql).
"""
from __future__ import annotations

import time
from textwrap import indent

from app.core.models import (
    ColumnIR,
    ConversionResult,
    ConversionStatus,
    ObjectType,
    TableIR,
    WarningLevel,
)
from app.core.settings import settings
from app.logging.logger import get_logger

log = get_logger("table_generator")

_INDENT = "    "   # 4-space indent — matches reference repo formatting


def generate_table(ir: TableIR, source_sql: str = "") -> ConversionResult:
    """
    Convert a TableIR into a Fabric T-SQL CREATE TABLE block.

    Returns a ConversionResult with the output SQL and all applied rules.
    """
    t0 = time.perf_counter()

    schema_placeholder = settings.flyway_schema_placeholder
    full_name = f"{schema_placeholder}.{ir.name}"

    lines: list[str] = []
    applied_rules: list[str] = []
    all_warnings = list(ir.warnings)

    # ── Idempotent wrapper ───────────────────────────────────────────────
    lines.append(f"IF OBJECT_ID('{full_name}', 'U') IS NULL")
    lines.append("BEGIN")

    # ── Column definitions ───────────────────────────────────────────────
    col_lines: list[str] = []
    for col in ir.columns:
        col_lines.append(_render_column(col, applied_rules))
        all_warnings.extend(col.warnings)

    col_block = (",\n" + _INDENT).join(col_lines)

    lines.append(f"{_INDENT}CREATE TABLE {full_name} (")
    lines.append(f"{_INDENT}{_INDENT}{col_block}")
    lines.append(f"{_INDENT});")
    lines.append("END;")

    output_sql = "\n".join(lines)

    # ── Strip rules applied ──────────────────────────────────────────────
    if ir.distkey or ir.diststyle:
        applied_rules.append("STRIP_DISTKEY_DISTSTYLE")
    if ir.sortkeys:
        applied_rules.append("STRIP_SORTKEY")
    applied_rules.append("IDEMPOTENT_CREATE_TABLE")
    applied_rules.append("SCHEMA_PARAMETERISATION")

    # ── Determine final status ───────────────────────────────────────────
    has_errors = any(w.level == WarningLevel.ERROR for w in all_warnings)
    has_warns = bool(all_warnings)

    if has_errors:
        status = ConversionStatus.MANUAL_REVIEW
        confidence = 0.50
    elif has_warns:
        status = ConversionStatus.PARTIAL
        confidence = 0.80
    else:
        status = ConversionStatus.HIGH_CONFIDENCE
        confidence = 1.0

    elapsed_ms = (time.perf_counter() - t0) * 1000

    unsupported = [w.code for w in all_warnings if "UNSUPPORTED" in w.code or w.level == WarningLevel.ERROR]
    manual_items = [w.message for w in all_warnings if w.level == WarningLevel.ERROR]

    return ConversionResult(
        source_name=f"{ir.schema}.{ir.name}",
        target_name=full_name,
        object_type=ObjectType.TABLE,
        status=status,
        confidence_score=confidence,
        source_sql=source_sql,
        output_sql=output_sql,
        warnings=all_warnings,
        applied_rules=list(dict.fromkeys(applied_rules)),  # dedupe, preserve order
        unsupported_features=unsupported,
        manual_review_items=manual_items,
        transform_time_ms=elapsed_ms,
    )


def _render_column(col: ColumnIR, applied_rules: list[str]) -> str:
    """Render a single column definition in Fabric T-SQL format."""
    # ── Column name quoting ──────────────────────────────────────────────
    if col.contains_spaces or col.is_reserved_word:
        col_name = f"[{col.name}]"
        applied_rules.append("BRACKET_QUOTE_IDENTIFIER")
    else:
        col_name = col.name

    # ── Type ────────────────────────────────────────────────────────────
    fabric_type = col.fabric_type or col.original_type.upper()

    # ── Nullability ─────────────────────────────────────────────────────
    null_clause = "" if col.is_nullable else " NOT NULL"

    # ── Default ─────────────────────────────────────────────────────────
    default_clause = f" DEFAULT {col.default_value}" if col.default_value else ""

    # ── ENCODE stripped ──────────────────────────────────────────────────
    if col.encode:
        applied_rules.append(f"STRIP_ENCODE_{col.encode}")

    return f"{col_name} {fabric_type}{null_clause}{default_clause}"
