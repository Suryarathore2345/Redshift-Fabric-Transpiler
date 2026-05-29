"""
Table DDL Generator  (v3 — inline warning comments)

What changed vs v2
──────────────────
FEATURE: Inline SQL comments on every column/line that has a warning.

Before:
    [geometry] VARCHAR(MAX),

After:
    [geometry] VARCHAR(MAX),          -- ⚠ REVIEW: GEOMETRY type unsupported in Fabric.
                                      --   Mapped to VARCHAR(MAX) as WKT string.
                                      --   💡 Spatial queries will NOT work. Consider Lakehouse.

Comment anatomy
───────────────
    <column_def>,   -- ⚠ REVIEW: <short one-line summary>   [for WARNING level]
                    --   <detail line 1>
                    --   💡 <suggestion>

    <column_def>,   -- ❌ MANUAL: <short summary>            [for ERROR level]
                    --   <detail>
                    --   💡 <suggestion>

    <column_def>,   -- ℹ TYPE MAPPED: BOOLEAN → BIT          [for AUTO/INFO mappings]

Header block also carries table-level warnings (INTERLEAVED SORTKEY,
TIMEZONE_STRIPPED at table level, etc.) as block comments before the
CREATE TABLE line.

Object-header block
────────────────────
Every generated object now gets a rich comment header:

    -- ════════════════════════════════════════════
    -- TABLE: bi_alefdw.map_polygons
    -- Target: ${schema}.map_polygons
    -- Status: PARTIAL  |  Confidence: 75%
    -- Warnings: 2  (see inline ⚠ markers below)
    -- ════════════════════════════════════════════
"""
from __future__ import annotations

import time
from textwrap import wrap

from app.core.models import (
    ColumnIR,
    ConversionResult,
    ConversionStatus,
    ConversionWarning,
    ObjectType,
    TableIR,
    WarningLevel,
)
from app.core.settings import settings
from app.logging.logger import get_logger

log = get_logger("table_generator")

_INDENT     = "    "     # 4-space indent  (one level)
_COL_INDENT = "        " # 8-space indent  (column lines inside CREATE TABLE)
_COMMENT_WRAP = 72       # max chars for wrapped comment detail lines


# ── Warning display config ────────────────────────────────────────────────────
#
# Maps warning codes → (icon, short label) used in inline comments.
# Any code not listed falls back to the generic ⚠ REVIEW label.

_WARNING_LABELS: dict[str, tuple[str, str]] = {
    # Type mapping warnings
    "DATATYPE_GEOMETRY":        ("⚠",  "UNSUPPORTED TYPE"),
    "DATATYPE_GEOGRAPHY":       ("⚠",  "UNSUPPORTED TYPE"),
    "DATATYPE_SUPER":           ("⚠",  "UNSUPPORTED TYPE"),
    "DATATYPE_VARBYTE":         ("⚠",  "TYPE MAPPED"),
    "DATATYPE_HLLSKETCH":       ("❌", "MANUAL REVIEW"),
    "DATATYPE_TEXT":            ("ℹ",  "TYPE MAPPED"),
    "TIMEZONE_STRIPPED":        ("⚠",  "TZ INFO LOST"),
    "UNKNOWN_DATATYPE":         ("⚠",  "UNKNOWN TYPE"),
    # Column-level
    "IDENTITY_COLUMN":          ("⚠",  "VERIFY IDENTITY"),
    # Table-level
    "INTERLEAVED_SORTKEY":      ("ℹ",  "CLAUSE STRIPPED"),
    # Validator residuals
    "RESIDUAL_GEOMETRY_TYPE":   ("⚠",  "UNSUPPORTED TYPE"),
    "RESIDUAL_BOOLEAN_TYPE":    ("⚠",  "TYPE NOT MAPPED"),
    # Generic fallback (applied in _inline_comment if code not found above)
    "__DEFAULT_WARN__":         ("⚠",  "REVIEW"),
    "__DEFAULT_ERROR__":        ("❌", "MANUAL REVIEW"),
    "__DEFAULT_INFO__":         ("ℹ",  "INFO"),
}


# ── Public API ────────────────────────────────────────────────────────────────


def generate_table(ir: TableIR, source_sql: str = "") -> ConversionResult:
    """
    Convert a TableIR into a Fabric T-SQL CREATE TABLE block
    with inline warning comments on every affected line.
    """
    t0 = time.perf_counter()

    schema_placeholder = settings.flyway_schema_placeholder
    full_name = f"{schema_placeholder}.{ir.name}"

    all_warnings: list[ConversionWarning] = list(ir.warnings)
    applied_rules: list[str] = []
    lines: list[str] = []

    # ── 1. Object header block ───────────────────────────────────────────
    # Computed after column pass; placeholder inserted after columns are done.
    # We build columns first so we know the final warning count.

    # ── 2. Build column lines ────────────────────────────────────────────
    col_lines: list[str] = []
    num_cols = len(ir.columns)
    for idx, col in enumerate(ir.columns):
        col_def = _render_column(col, applied_rules)
        all_warnings.extend(col.warnings)
        is_last = (idx == num_cols - 1)
        # Comma goes ON the column definition line (before any comment lines)
        # so that comment lines never carry a trailing comma
        col_def_with_comma = col_def if is_last else col_def + ","

        if col.warnings:
            col_lines.append(_annotate_column_line(col_def_with_comma, col.warnings))
        else:
            col_lines.append(f"{_COL_INDENT}{col_def_with_comma}")

    # ── 3. Determine status & confidence ────────────────────────────────
    has_errors  = any(w.level == WarningLevel.ERROR   for w in all_warnings)
    has_warns   = any(w.level == WarningLevel.WARNING for w in all_warnings)

    if has_errors:
        status     = ConversionStatus.MANUAL_REVIEW
        confidence = 0.50
    elif has_warns:
        status     = ConversionStatus.PARTIAL
        confidence = 0.80
    else:
        status     = ConversionStatus.HIGH_CONFIDENCE
        confidence = 1.0

    # ── 4. Object header (now we know warning count & status) ────────────
    header = _object_header(
        source_name=f"{ir.schema}.{ir.name}",
        target_name=full_name,
        status=status,
        confidence=confidence,
        warnings=all_warnings,
    )
    lines.extend(header)

    # ── 5. Table-level warning block (e.g. INTERLEAVED_SORTKEY) ─────────
    table_level_warns = [w for w in ir.warnings]   # ir.warnings = table-level only
    if table_level_warns:
        lines.append(f"-- {'─' * 60}")
        lines.append("-- ⚡ TABLE-LEVEL CONVERSION NOTES:")
        for w in table_level_warns:
            icon, label = _get_label(w)
            lines.append(f"--   {icon} {label}: {w.message}")
            if w.suggestion:
                lines.append(f"--   💡 {w.suggestion}")
        lines.append(f"-- {'─' * 60}")

    # ── 6. Idempotent CREATE TABLE wrapper ───────────────────────────────
    lines.append(f"IF OBJECT_ID('{full_name}', 'U') IS NULL")
    lines.append("BEGIN")
    lines.append(f"{_INDENT}CREATE TABLE {full_name} (")
    lines.append("\n".join(col_lines))
    lines.append(f"{_INDENT});")
    lines.append("END;")

    output_sql = "\n".join(lines)

    # ── 7. Applied rules ─────────────────────────────────────────────────
    if ir.distkey or ir.diststyle:
        applied_rules.append("STRIP_DISTKEY_DISTSTYLE")
    if ir.sortkeys:
        applied_rules.append("STRIP_SORTKEY")
    applied_rules.append("IDEMPOTENT_CREATE_TABLE")
    applied_rules.append("SCHEMA_PARAMETERISATION")
    applied_rules.append("INLINE_WARNING_COMMENTS")

    elapsed_ms = (time.perf_counter() - t0) * 1000

    unsupported  = [w.code for w in all_warnings if "UNSUPPORTED" in w.code or w.level == WarningLevel.ERROR]
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
        applied_rules=list(dict.fromkeys(applied_rules)),
        unsupported_features=unsupported,
        manual_review_items=manual_items,
        transform_time_ms=elapsed_ms,
    )


# ── Column rendering ──────────────────────────────────────────────────────────


def _render_column(col: ColumnIR, applied_rules: list[str]) -> str:
    """Render a single column definition (without indentation or comments)."""
    # Name — bracket-quote if reserved word or contains spaces
    if col.contains_spaces or col.is_reserved_word:
        col_name = f"[{col.name}]"
        applied_rules.append("BRACKET_QUOTE_IDENTIFIER")
    else:
        col_name = col.name

    fabric_type    = col.fabric_type or col.original_type.upper()
    null_clause    = "" if col.is_nullable else " NOT NULL"
    default_clause = f" DEFAULT {col.default_value}" if col.default_value else ""

    if col.encode:
        applied_rules.append(f"STRIP_ENCODE_{col.encode}")

    return f"{col_name} {fabric_type}{null_clause}{default_clause}"


def _annotate_column_line(col_def: str, warnings: list[ConversionWarning]) -> str:
    """
    Attach inline SQL warning comments to a column definition line.

    Design rules:
      - The first warning label goes on the SAME line as the column def,
        right-aligned at column 64 for readability.
      - All warning messages are written in FULL — no truncation ever.
      - Long messages are wrapped onto continuation comment lines.
      - Suggestion lines are prefixed with 💡.
      - Additional warnings (if a column has >1) each get their own block.

    Example output:
        [geometry] VARCHAR(MAX),                -- ⚠ UNSUPPORTED TYPE: Redshift GEOMETRY type is
                                                --   unsupported in Fabric Warehouse. Mapped to
                                                --   VARCHAR(MAX) as WKT string.
                                                --   💡 Spatial queries will NOT work. Consider
                                                --   Lakehouse for geometry data.
    """
    # Column comment alignment — pad col def to this width before the --
    ALIGN_COL   = 64
    # Comment continuation indent (must match ALIGN_COL + "-- " width)
    CONT_INDENT = " " * ALIGN_COL + "--   "
    SUGG_INDENT = " " * ALIGN_COL + "--   💡 "
    NEXT_INDENT = " " * ALIGN_COL + "--   "
    WRAP_WIDTH  = 100   # total line width before wrapping comment text

    result_lines: list[str] = []

    for i, w in enumerate(warnings):
        icon, label = _get_label(w)
        full_msg    = w.message.strip()
        full_sugg   = w.suggestion.strip() if w.suggestion else ""

        if i == 0:
            # ── First warning: same line as column def ────────────────
            base_line = f"{_COL_INDENT}{col_def}"
            padding   = max(1, ALIGN_COL - len(base_line))
            # First line of the comment
            comment_prefix = f"{' ' * padding}-- {icon} {label}: "
            available = WRAP_WIDTH - ALIGN_COL - len(comment_prefix.lstrip())

            # Wrap full message — never truncate
            msg_lines = wrap(full_msg, width=max(40, available)) if full_msg else [""]
            result_lines.append(f"{base_line}{comment_prefix}{msg_lines[0]}")
            for cont in msg_lines[1:]:
                result_lines.append(f"{CONT_INDENT}{cont}")
        else:
            # ── Additional warnings: their own comment block ───────────
            comment_prefix = f"{' ' * ALIGN_COL}-- {icon} {label}: "
            available = WRAP_WIDTH - ALIGN_COL - len(comment_prefix.lstrip())
            msg_lines = wrap(full_msg, width=max(40, available)) if full_msg else [""]
            result_lines.append(f"{comment_prefix}{msg_lines[0]}")
            for cont in msg_lines[1:]:
                result_lines.append(f"{CONT_INDENT}{cont}")

        # ── Suggestion (always full text, wrapped) ────────────────────
        if full_sugg and full_sugg != full_msg:
            sugg_available = WRAP_WIDTH - ALIGN_COL - len("--   💡 ")
            sugg_lines = wrap(full_sugg, width=max(40, sugg_available))
            result_lines.append(f"{' ' * ALIGN_COL}--   💡 {sugg_lines[0]}")
            for cont in sugg_lines[1:]:
                result_lines.append(f"{NEXT_INDENT}{cont}")

    return "\n".join(result_lines)


# ── Object header ─────────────────────────────────────────────────────────────


def _object_header(
    source_name: str,
    target_name: str,
    status: ConversionStatus,
    confidence: float,
    warnings: list[ConversionWarning],
) -> list[str]:
    """
    Generate the rich comment block that precedes each CREATE TABLE.

    Example:
        -- ════════════════════════════════════════════════════════════════
        -- TABLE  : bi_alefdw.map_polygons
        -- Target : ${schema}.map_polygons
        -- Status : PARTIAL  |  Confidence: 80%
        -- Warnings: 1  ← see ⚠ markers inline on affected columns
        -- ════════════════════════════════════════════════════════════════
    """
    status_icon = {
        ConversionStatus.HIGH_CONFIDENCE: "✅",
        ConversionStatus.PARTIAL:         "⚠️ ",
        ConversionStatus.MANUAL_REVIEW:   "🔍",
        ConversionStatus.FAILED:          "❌",
        ConversionStatus.UNSUPPORTED:     "🚫",
    }.get(status, "❓")

    warn_count = len(warnings)
    warn_note  = (
        f"{warn_count}  ← see ⚠ markers inline on affected columns/lines"
        if warn_count > 0 else "0  ← clean conversion"
    )

    border = "═" * 66
    return [
        f"-- {border}",
        f"-- TABLE  : {source_name}",
        f"-- Target : {target_name}",
        f"-- Status : {status_icon} {status.value}  |  Confidence: {confidence:.0%}",
        f"-- Warnings: {warn_note}",
        f"-- {border}",
    ]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_label(w: ConversionWarning) -> tuple[str, str]:
    """Return (icon, label) for a warning, using the warning code lookup table."""
    if w.code in _WARNING_LABELS:
        return _WARNING_LABELS[w.code]
    # Fallback by level
    if w.level == WarningLevel.ERROR:
        return _WARNING_LABELS["__DEFAULT_ERROR__"]
    if w.level == WarningLevel.WARNING:
        return _WARNING_LABELS["__DEFAULT_WARN__"]
    return _WARNING_LABELS["__DEFAULT_INFO__"]


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis if longer than max_len."""
    return text if len(text) <= max_len else text[:max_len - 1] + "…"
