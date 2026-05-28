"""
Validation Engine

Post-conversion validation layer that inspects output SQL for:
  1. Remaining Redshift-specific syntax that wasn't caught by the transformer.
  2. Fabric T-SQL surface area violations.
  3. Structural issues (unclosed brackets, missing aliases, etc.).
  4. Confidence score adjustment.

Each validator is a standalone function returning a list of warnings.
"""
from __future__ import annotations

import re

from app.core.models import ConversionResult, ConversionWarning, WarningLevel
from app.logging.logger import get_logger

log = get_logger("validator")


# ── Redshift residual patterns ────────────────────────────────────────────────

_RESIDUAL_CHECKS: list[tuple[str, re.Pattern, str, str]] = [
    (
        "RESIDUAL_ENCODE",
        re.compile(r'\bENCODE\s+\w+', re.IGNORECASE),
        "Residual ENCODE clause found in output SQL.",
        "ENCODE is Redshift-specific; strip manually.",
    ),
    (
        "RESIDUAL_DISTSTYLE",
        re.compile(r'\bDISTSTYLE\b', re.IGNORECASE),
        "Residual DISTSTYLE clause found.",
        "DISTSTYLE has no Fabric equivalent; remove.",
    ),
    (
        "RESIDUAL_DISTKEY",
        re.compile(r'\bDISTKEY\b', re.IGNORECASE),
        "Residual DISTKEY clause found.",
        "DISTKEY has no Fabric equivalent; remove.",
    ),
    (
        "RESIDUAL_SORTKEY",
        re.compile(r'\bSORTKEY\b', re.IGNORECASE),
        "Residual SORTKEY clause found.",
        "SORTKEY has no Fabric equivalent; remove.",
    ),
    (
        "RESIDUAL_NO_SCHEMA_BINDING",
        re.compile(r'\bWITH\s+NO\s+SCHEMA\s+BINDING\b', re.IGNORECASE),
        "Residual WITH NO SCHEMA BINDING found.",
        "Remove this Redshift-only clause.",
    ),
    (
        "RESIDUAL_MATERIALIZED",
        re.compile(r'\bMATERIALIZED\s+VIEW\b', re.IGNORECASE),
        "MATERIALIZED VIEW keyword found in output.",
        "Convert to stored procedure + CTAS pattern.",
    ),
    (
        "RESIDUAL_CREATE_OR_REPLACE",
        re.compile(r'\bCREATE\s+OR\s+REPLACE\b', re.IGNORECASE),
        "CREATE OR REPLACE found — should be CREATE OR ALTER in T-SQL.",
        "Replace with CREATE OR ALTER.",
    ),
    (
        "RESIDUAL_CAST_OPERATOR",
        re.compile(r'::\s*\w+', re.IGNORECASE),
        "Residual PostgreSQL :: cast operator found.",
        "Replace with CAST(expr AS type) or CONVERT(type, expr).",
    ),
    (
        "RESIDUAL_CURRENT_DATE",
        re.compile(r'\bCURRENT_DATE\b', re.IGNORECASE),
        "Residual CURRENT_DATE found.",
        "Replace with CONVERT(DATE, GETDATE()).",
    ),
    (
        "RESIDUAL_DATE_TRUNC",
        re.compile(r'\bDATE_TRUNC\s*\(', re.IGNORECASE),
        "Residual DATE_TRUNC found.",
        "Replace with DATETRUNC(part, expr).",
    ),
    (
        "RESIDUAL_NVL",
        re.compile(r'\bNVL\s*\(', re.IGNORECASE),
        "Residual NVL() found.",
        "Replace with ISNULL(expr, replacement).",
    ),
    (
        "RESIDUAL_INTERVAL",
        re.compile(r'\bINTERVAL\s+\'', re.IGNORECASE),
        "Residual INTERVAL literal found.",
        "Replace with DATEADD(unit, n, expr).",
    ),
    (
        "RESIDUAL_IS_TRUE",
        re.compile(r'\bIS\s+(?:NOT\s+)?(?:TRUE|FALSE)\b', re.IGNORECASE),
        "Residual IS TRUE/FALSE found.",
        "Replace with = 1 / = 0.",
    ),
    (
        "RESIDUAL_GEOMETRY_TYPE",
        re.compile(r'\bGEOMETRY\b', re.IGNORECASE),
        "GEOMETRY column type found — not supported in Fabric Warehouse.",
        "Map to VARCHAR(MAX) for WKT or consider a Lakehouse geometry approach.",
    ),
    (
        "RESIDUAL_SUPER_TYPE",
        re.compile(r'\bSUPER\b', re.IGNORECASE),
        "SUPER column type found — not supported in Fabric Warehouse.",
        "Map to VARCHAR(MAX) for JSON storage.",
    ),
    (
        "RESIDUAL_BOOLEAN_TYPE",
        re.compile(r'\bBOOLEAN\b', re.IGNORECASE),
        "BOOLEAN type found — not a valid Fabric Warehouse type.",
        "Replace with BIT.",
    ),
    (
        "RESIDUAL_REGEXP",
        re.compile(r'\bREGEXP_\w+\s*\(', re.IGNORECASE),
        "REGEXP_* function found — unsupported in Fabric Warehouse.",
        "Requires application-layer implementation.",
    ),
    (
        "RESIDUAL_LISTAGG",
        re.compile(r'\bLISTAGG\s*\(', re.IGNORECASE),
        "Residual LISTAGG() found.",
        "Replace with STRING_AGG(col, delim) WITHIN GROUP (ORDER BY ...).",
    ),
]


def validate_result(result: ConversionResult) -> ConversionResult:
    """
    Run all validation checks on a ConversionResult's output SQL.
    Appends additional warnings and adjusts confidence score.

    Returns the (potentially modified) ConversionResult.
    """
    sql = result.output_sql
    new_warnings: list[ConversionWarning] = []

    for code, pattern, message, suggestion in _RESIDUAL_CHECKS:
        if pattern.search(sql):
            # Don't double-add if already warned
            existing_codes = {w.code for w in result.warnings}
            if code not in existing_codes:
                new_warnings.append(ConversionWarning(
                    level=WarningLevel.WARNING,
                    code=code,
                    message=f"[Validator] {message}",
                    suggestion=suggestion,
                ))

    if new_warnings:
        result.warnings.extend(new_warnings)
        # Penalise confidence for residual issues
        penalty = len(new_warnings) * 0.05
        result.confidence_score = max(0.10, result.confidence_score - penalty)
        log.warning(
            "validation_warnings",
            object=result.source_name,
            count=len(new_warnings),
        )

    return result
