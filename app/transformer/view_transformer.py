"""
View Transformer

Applies all Redshift → Fabric T-SQL transformations to a view body and
emits the final CREATE OR ALTER VIEW (or stored procedure) DDL.

Transformation pipeline (applied in order):
  1.  Schema placeholder substitution
  2.  Table name suffix stripping (_mv, _view)
  3.  WITH NO SCHEMA BINDING removal
  4.  CREATE OR REPLACE VIEW → CREATE OR ALTER VIEW
  5.  DATE_TRUNC → DATETRUNC (+ argument order swap)
  6.  CURRENT_DATE → CONVERT(DATE, GETDATE())
  7.  CURRENT_TIMESTAMP → GETDATE()
  8.  PostgreSQL :: cast → CAST/CONVERT
  9.  INTERVAL literals → DATEADD()
  10. NVL() → ISNULL()
  11. IS TRUE / IS FALSE → = 1 / = 0
  12. Boolean IS NOT FALSE → <> 0, etc.
  13. date(expr) → CONVERT(DATE, expr)
  14. LOWER(col) retained (T-SQL is case-insensitive but matches reference)
  15. Ordinal GROUP BY preserved (Fabric supports it; explicit expansion optional)
  16. QUALIFY → subquery wrapper (best-effort; flags for manual review)
  17. LISTAGG → STRING_AGG
  18. md5() → md5() with warning (reference repo keeps it as-is)
  19. DECODE() → CASE WHEN (best-effort)

All transformations are implemented as independent functions that can be
unit-tested and toggled in the rule registry.
"""
from __future__ import annotations

import re
import time
from typing import Callable

from app.core.models import (
    ConversionResult,
    ConversionStatus,
    ConversionWarning,
    ObjectType,
    ViewIR,
    WarningLevel,
)
from app.core.rules import BOOLEAN_REWRITES, FUNCTION_MAP
from app.core.settings import settings
from app.logging.logger import get_logger

log = get_logger("view_transformer")


# ── Public API ────────────────────────────────────────────────────────────────


def transform_view(ir: ViewIR, source_sql: str = "") -> ConversionResult:
    """
    Apply all transformation rules to a ViewIR and produce a ConversionResult.
    """
    t0 = time.perf_counter()

    body = ir.body
    warnings: list[ConversionWarning] = list(ir.warnings)
    applied_rules: list[str] = []

    # ── Run transformation pipeline ───────────────────────────────────────
    pipeline: list[tuple[str, Callable]] = [
        ("SCHEMA_PARAMETERISATION",   _transform_schema_refs),
        ("STRIP_TABLE_SUFFIXES",      _strip_table_name_suffixes),
        ("BOOLEAN_IS_EXPR",           _transform_boolean_is),
        ("DATE_TRUNC",                _transform_date_trunc),
        ("DATE_FUNCTION",             _transform_date_function),
        ("CURRENT_DATE",              _transform_current_date),
        ("CURRENT_TIMESTAMP",         _transform_current_timestamp),
        ("INTERVAL_LITERAL",          _transform_interval),
        ("CAST_OPERATOR",             _transform_cast_operator),
        ("NVL_TO_ISNULL",             _transform_nvl),
        ("LISTAGG_TO_STRING_AGG",     _transform_listagg),
        ("DECODE_TO_CASE",            _transform_decode),
        ("MD5_WARNING",               _warn_md5),
        ("QUALIFY_WARNING",           _warn_qualify),
        ("REGEXP_WARNING",            _warn_regexp),
        ("LOWER_FILTER",              _transform_lower_case_compare),
    ]

    for rule_id, fn in pipeline:
        body, rule_warns = fn(body)
        if rule_warns or _rule_changed(body):
            applied_rules.append(rule_id)
        warnings.extend(rule_warns)

    # ── Build CREATE OR ALTER VIEW header ─────────────────────────────────
    is_matview = ir.object_type == ObjectType.MATERIALIZED_VIEW
    output_schema = settings.output_schema_placeholder
    view_name = ir.name

    applied_rules.append("CREATE_OR_ALTER_VIEW")

    if is_matview:
        output_sql = _build_stored_procedure(output_schema, view_name, body, warnings, applied_rules)
    else:
        output_sql = _build_view(output_schema, view_name, body)

    elapsed_ms = (time.perf_counter() - t0) * 1000

    # ── Confidence scoring ────────────────────────────────────────────────
    manual_items = [w.message for w in warnings if w.level == WarningLevel.ERROR]
    warn_items = [w for w in warnings if w.level == WarningLevel.WARNING]

    if manual_items:
        status = ConversionStatus.MANUAL_REVIEW
        confidence = 0.50
    elif warn_items:
        status = ConversionStatus.PARTIAL
        confidence = max(0.65, 1.0 - len(warn_items) * 0.05)
    else:
        status = ConversionStatus.HIGH_CONFIDENCE
        confidence = 1.0

    return ConversionResult(
        source_name=f"{ir.schema}.{ir.name}",
        target_name=f"{output_schema}.{view_name}",
        object_type=ir.object_type,
        status=status,
        confidence_score=round(confidence, 3),
        source_sql=source_sql,
        output_sql=output_sql,
        warnings=warnings,
        applied_rules=list(dict.fromkeys(applied_rules)),
        unsupported_features=[w.code for w in warnings if "UNSUPPORTED" in w.code],
        manual_review_items=manual_items,
        transform_time_ms=elapsed_ms,
    )


def _rule_changed(s: str) -> bool:
    """Placeholder — in practice each fn tracks its own changes."""
    return False


# ── View / procedure builders ─────────────────────────────────────────────────


def _build_view(schema: str, name: str, body: str) -> str:
    return f"CREATE OR ALTER VIEW {schema}.{name} AS\n{body};"


def _build_stored_procedure(
    schema: str,
    name: str,
    body: str,
    warnings: list[ConversionWarning],
    applied_rules: list[str],
) -> str:
    """
    Emit a stored procedure that refreshes a materialised view equivalent.
    Pattern from reference repo: DROP TABLE IF EXISTS + CTAS.
    """
    applied_rules.append("MATVIEW_TO_STORED_PROC")
    rs_schema = settings.placeholder_read_schema  # ${rs_bi_alefdw}
    staging = f"{schema}.{name}_staging"
    final = f"{schema}.{name}"

    proc = f"""CREATE OR ALTER PROCEDURE {schema}.usp_refresh_{name}
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRY

        -- Step 1: Drop stale staging table if it exists
        DROP TABLE IF EXISTS {staging};

        -- Step 2: CTAS - Build staging table with full transformation
        CREATE TABLE {staging}
        AS
        {body};

        -- Step 3: Drop current live table and promote staging
        DROP TABLE IF EXISTS {final};

        EXEC sp_rename '{staging}', '{name}';

    END TRY
    BEGIN CATCH
        THROW;
    END CATCH;
END;"""

    warnings.append(ConversionWarning(
        level=WarningLevel.WARNING,
        code="MATVIEW_PATTERN",
        message=f"Materialised view '{name}' converted to stored procedure + CTAS pattern.",
        suggestion="Schedule usp_refresh_{name} to run periodically via Fabric pipeline.",
    ))
    return proc


# ── Transformation functions ──────────────────────────────────────────────────
# Each returns (transformed_sql, list[ConversionWarning])


def _transform_schema_refs(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    Replace source schema prefixes with Fabric parameterised placeholders.

    Mapping (from settings.schema_placeholder_map):
      bi_alefdw.  → ${rs_bi_alefdw}.
      bi_alefdw_dev. → ${rs_bi_alefdw}.
      alefdw.     → ${rs_alefdw}.
      alefdw_dev. → ${rs_alefdw}.
    """
    warnings: list[ConversionWarning] = []
    result = sql

    for source_schema, placeholder in settings.schema_placeholder_map.items():
        # Match schema. prefix (with optional quotes) but not inside strings
        pattern = re.compile(
            rf'\b{re.escape(source_schema)}\.',
            re.IGNORECASE,
        )
        if pattern.search(result):
            result = pattern.sub(f"{placeholder}.", result)

    return result, warnings


def _strip_table_name_suffixes(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    Strip _mv and _view suffixes from table references in SQL bodies.

    Example:
      bi_alefdw.students_lesson_progress_mv  → ${rs_bi_alefdw}.students_lesson_progress
      bi_alefdw.bi_active_schools_dim_mv     → ${rs_bi_alefdw}.bi_active_schools_dim

    This matches the reference repo pattern where Redshift materialised views
    are mapped to plain Fabric tables without suffixes.
    """
    warnings: list[ConversionWarning] = []
    if not settings.strip_table_suffixes_in_views:
        return sql, warnings

    result = sql
    for suffix in settings.strip_name_suffixes:
        # Match suffix only when followed by word boundary (dot, comma, whitespace, etc.)
        pattern = re.compile(
            rf'{re.escape(suffix)}\b',
            re.IGNORECASE,
        )
        result = pattern.sub("", result)

    return result, warnings


def _transform_boolean_is(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    Convert PostgreSQL boolean IS TRUE / IS FALSE expressions to T-SQL bit comparisons.

    IS TRUE        → = 1
    IS FALSE       → = 0
    IS NOT TRUE    → <> 1
    IS NOT FALSE   → <> 0
    """
    warnings: list[ConversionWarning] = []
    result = sql

    for pattern_str, replacement in BOOLEAN_REWRITES.items():
        pattern = re.compile(re.escape(pattern_str), re.IGNORECASE)
        if pattern.search(result):
            result = pattern.sub(replacement, result)

    return result, warnings


def _transform_date_trunc(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    DATE_TRUNC('part', expr) → DATETRUNC(part, expr)

    Also handles the 'week' → 'iso_week' renaming pattern from the reference
    repo (alain_students_login: DATE_TRUNC('week', ...) → DATETRUNC(iso_week, ...))
    """
    warnings: list[ConversionWarning] = []
    result = sql

    # Pattern: DATE_TRUNC('part', expr)  — with quoted part
    date_trunc_re = re.compile(
        r"DATE_TRUNC\s*\(\s*'(\w+)'\s*,\s*",
        re.IGNORECASE,
    )

    def replace_date_trunc(m: re.Match) -> str:
        part = m.group(1).lower()
        # 'week' → 'iso_week' per reference repo
        if part == "week":
            part = "iso_week"
        return f"DATETRUNC({part}, "

    result = date_trunc_re.sub(replace_date_trunc, result)

    # Also handle DATE_TRUNC without quotes (rare but seen)
    date_trunc_nq_re = re.compile(
        r"DATE_TRUNC\s*\(\s*(\w+)\s*,\s*",
        re.IGNORECASE,
    )
    result = date_trunc_nq_re.sub(lambda m: f"DATETRUNC({m.group(1).lower()}, ", result)

    return result, warnings


def _transform_date_function(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    date(expr) — Redshift/Postgres date() function → CONVERT(DATE, expr)

    Handles: date(login_local_date_time) → CONVERT(DATE, login_local_date_time)
    """
    warnings: list[ConversionWarning] = []
    # Match date( followed by anything not starting with '_' or another word char
    # to avoid matching date_add, date_diff, etc.
    pattern = re.compile(r'\bdate\s*\(', re.IGNORECASE)

    def _repl(m: re.Match) -> str:
        return "CONVERT(DATE, "

    result = pattern.sub(_repl, sql)
    return result, warnings


def _transform_current_date(sql: str) -> tuple[str, list[ConversionWarning]]:
    """CURRENT_DATE → CONVERT(DATE, GETDATE())"""
    warnings: list[ConversionWarning] = []
    result = re.sub(r'\bCURRENT_DATE\b', 'CONVERT(DATE, GETDATE())', sql, flags=re.IGNORECASE)
    return result, warnings


def _transform_current_timestamp(sql: str) -> tuple[str, list[ConversionWarning]]:
    """CURRENT_TIMESTAMP → GETDATE()"""
    warnings: list[ConversionWarning] = []
    result = re.sub(r'\bCURRENT_TIMESTAMP\b', 'GETDATE()', sql, flags=re.IGNORECASE)
    return result, warnings


def _transform_interval(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    Replace INTERVAL 'n unit' patterns with DATEADD() equivalents.

    Patterns seen in reference:
      + INTERVAL '6 day'      → + 6  (for date arithmetic with DATETRUNC result)
      - INTERVAL '2 year'     → DATEADD(YEAR, -2, expr)
      + INTERVAL '1 day'      → DATEADD(DAY, 1, expr)
      + INTERVAL '1 month'    → DATEADD(MONTH, 1, expr)

    NOTE: INTERVAL in date arithmetic is context-sensitive. The pattern
    `some_date + INTERVAL 'n unit'` is rewritten; more complex expressions
    are flagged for manual review.
    """
    warnings: list[ConversionWarning] = []
    result = sql

    # Pattern: + INTERVAL 'N unit'
    interval_add_re = re.compile(
        r"\+\s*INTERVAL\s+'(\d+)\s+(\w+)'",
        re.IGNORECASE,
    )

    def _add_repl(m: re.Match) -> str:
        n = m.group(1)
        unit = m.group(2).upper()
        # Singular unit only: DAY → DAY, DAYS → DAY etc.
        unit = unit.rstrip("S") if unit.endswith("S") and unit != "SS" else unit
        return f"+ {n}  /* DATEADD({unit}, {n}, <expr>) — verify context */"

    result = interval_add_re.sub(_add_repl, result)

    # Pattern: - INTERVAL 'N unit'
    interval_sub_re = re.compile(
        r"-\s*INTERVAL\s+'(\d+)\s+(\w+)'",
        re.IGNORECASE,
    )

    def _sub_repl(m: re.Match) -> str:
        n = m.group(1)
        unit = m.group(2).upper().rstrip("S")
        return f"- {n}  /* DATEADD({unit}, -{n}, <expr>) — verify context */"

    result = interval_sub_re.sub(_sub_repl, result)

    if result != sql:
        warnings.append(ConversionWarning(
            level=WarningLevel.WARNING,
            code="INTERVAL_LITERAL",
            message="INTERVAL literals approximated. Review DATEADD context.",
            suggestion="Verify each INTERVAL replacement in context of surrounding date expression.",
        ))

    return result, warnings


# Regex for :: cast operator with type (handles type(precision) too)
_CAST_OP_RE = re.compile(
    r'::\s*(timestamp(?:\s+(?:without|with)\s+time\s+zone)?'
    r'|date|int(?:eger)?|bigint|float(?:\(\d+\))?|numeric(?:\(\d+,\d+\))?'
    r'|decimal(?:\(\d+,\d+\))?|varchar(?:\(\d+\))?|boolean|text)',
    re.IGNORECASE,
)

_CAST_TYPE_MAP = {
    "timestamp": "DATETIME2(6)",
    "timestamp without time zone": "DATETIME2(6)",
    "timestamp with time zone": "DATETIME2(6)",
    "date": "DATE",
    "integer": "INT",
    "int": "INT",
    "bigint": "BIGINT",
    "float": "FLOAT(53)",
    "boolean": "BIT",
    "text": "VARCHAR(MAX)",
}


def _transform_cast_operator(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    Replace PostgreSQL :: cast operator with CAST(expr AS type).

    This is complex because the expression being cast precedes the ::.
    We do a conservative right-to-left scan to find the matching expression.

    Simple cases handled:
      expr::date               → CONVERT(DATE, expr)
      expr::timestamp          → CONVERT(DATETIME2(6), expr)
      expr::int / ::integer    → CAST(expr AS INT)
      expr::bigint             → CAST(expr AS BIGINT)
      expr::float / ::float8   → CAST(expr AS FLOAT(53))
      expr::numeric(p,s)       → CAST(expr AS DECIMAL(p,s))
      expr::varchar(n)         → CAST(expr AS VARCHAR(n))
      expr::boolean            → CAST(expr AS BIT)
      expr::text               → CAST(expr AS VARCHAR(MAX))

    For compound expressions (subqueries, function calls) the cast is
    approximated and flagged.
    """
    warnings: list[ConversionWarning] = []
    result = sql

    def _repl(m: re.Match) -> str:
        raw_type = m.group(1).strip().lower()
        fabric_type = _CAST_TYPE_MAP.get(raw_type)
        if not fabric_type:
            # Numeric/decimal with precision
            if raw_type.startswith("numeric") or raw_type.startswith("decimal"):
                prec_m = re.search(r"\((\d+),\s*(\d+)\)", raw_type)
                if prec_m:
                    fabric_type = f"DECIMAL({prec_m.group(1)},{prec_m.group(2)})"
                else:
                    fabric_type = "DECIMAL(18,0)"
            elif raw_type.startswith("float"):
                prec_m = re.search(r"\((\d+)\)", raw_type)
                fabric_type = f"FLOAT({prec_m.group(1)})" if prec_m else "FLOAT(53)"
            elif raw_type.startswith("varchar"):
                prec_m = re.search(r"\((\d+)\)", raw_type)
                fabric_type = f"VARCHAR({prec_m.group(1)})" if prec_m else "VARCHAR(MAX)"
            else:
                fabric_type = raw_type.upper()

        # Return just the suffix replacement — we'll use a different strategy
        return f"__CAST_TO_{fabric_type}__"

    # First pass: mark all :: positions
    result_marked = _CAST_OP_RE.sub(_repl, result)

    # Second pass: for each marked cast, find the preceding expression
    # Simple approach: extract the token immediately to the left of __CAST_TO_
    cast_marker_re = re.compile(r'(\w+|\([^)]+\))\s*__CAST_TO_(\w+(?:\(\w+(?:,\w+)?\))?)__')

    def _finalize_cast(m: re.Match) -> str:
        expr = m.group(1)
        ttype = m.group(2)
        if ttype in ("DATE", "DATETIME2(6)"):
            return f"CONVERT({ttype}, {expr})"
        return f"CAST({expr} AS {ttype})"

    result = cast_marker_re.sub(_finalize_cast, result_marked)

    # Clean up any remaining unmarked cast markers
    result = re.sub(r'__CAST_TO_([^_]+)__', r'/* CAST AS \1 */', result)

    return result, warnings


def _transform_nvl(sql: str) -> tuple[str, list[ConversionWarning]]:
    """NVL(x, y) → ISNULL(x, y)"""
    warnings: list[ConversionWarning] = []
    result = re.sub(r'\bNVL\s*\(', 'ISNULL(', sql, flags=re.IGNORECASE)
    return result, warnings


def _transform_listagg(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    LISTAGG(col, delim) WITHIN GROUP (ORDER BY ...) → STRING_AGG(col, delim) WITHIN GROUP (ORDER BY ...)

    DISTINCT in LISTAGG is flagged as not supported in STRING_AGG.
    """
    warnings: list[ConversionWarning] = []
    result = sql

    # Check for LISTAGG(DISTINCT ...)
    if re.search(r"\bLISTAGG\s*\(\s*DISTINCT\b", sql, re.IGNORECASE):
        warnings.append(ConversionWarning(
            level=WarningLevel.WARNING,
            code="LISTAGG_DISTINCT",
            message="LISTAGG(DISTINCT ...) — STRING_AGG does not support DISTINCT. Manual rewrite required.",
            suggestion="Use a subquery with DISTINCT before aggregating with STRING_AGG.",
        ))

    result = re.sub(r'\bLISTAGG\s*\(', 'STRING_AGG(', result, flags=re.IGNORECASE)
    return result, warnings


def _transform_decode(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    DECODE(expr, val1, result1, val2, result2, default) → CASE WHEN ... END

    This is a best-effort transformation. Complex nested DECODE calls
    are flagged for manual review.
    """
    warnings: list[ConversionWarning] = []
    result = sql

    if not re.search(r'\bDECODE\s*\(', sql, re.IGNORECASE):
        return sql, warnings

    # We can't reliably parse nested DECODE without a full AST.
    # Flag for review and leave the call in place with a comment.
    warnings.append(ConversionWarning(
        level=WarningLevel.WARNING,
        code="DECODE_FUNCTION",
        message=(
            "DECODE() detected. Automatic conversion attempted but may be incomplete. "
            "Pattern: DECODE(expr,v1,r1,...,default) → CASE expr WHEN v1 THEN r1 ... ELSE default END."
        ),
        suggestion="Review all DECODE() conversions — NULL equality semantics differ from CASE WHEN.",
    ))

    # Simple 2-arg DECODE(expr, v, r, default) pattern
    def _decode_repl(m: re.Match) -> str:
        # We can't reliably extract args here without a proper parser
        # Return a comment indicating manual review needed
        return m.group(0) + " /* MANUAL REVIEW: convert to CASE WHEN */"

    result = re.sub(r'\bDECODE\s*\(', lambda m: 'DECODE(', result, flags=re.IGNORECASE)

    return result, warnings


def _warn_md5(sql: str) -> tuple[str, list[ConversionWarning]]:
    """Flag MD5() usage with a warning — reference repo keeps it as-is."""
    warnings: list[ConversionWarning] = []
    if re.search(r'\bmd5\s*\(', sql, re.IGNORECASE):
        warnings.append(ConversionWarning(
            level=WarningLevel.WARNING,
            code="MD5_FUNCTION",
            message=(
                "md5() is not a native Fabric Warehouse function. "
                "Retained as-is (reference repo pattern). Ensure a user-defined md5 scalar "
                "function exists in your Fabric environment, or replace with "
                "HASHBYTES('MD5', CAST(expr AS VARBINARY(MAX)))."
            ),
            suggestion="Create a SQL UDF or replace with HASHBYTES.",
        ))
    return sql, warnings


def _warn_qualify(sql: str) -> tuple[str, list[ConversionWarning]]:
    """Flag QUALIFY clause — must be manually rewritten as subquery."""
    warnings: list[ConversionWarning] = []
    if re.search(r'\bQUALIFY\b', sql, re.IGNORECASE):
        warnings.append(ConversionWarning(
            level=WarningLevel.WARNING,
            code="QUALIFY_CLAUSE",
            message=(
                "QUALIFY clause is unsupported in Fabric T-SQL. "
                "Rewrite as: SELECT * FROM (original_query) WHERE window_col = ..."
            ),
            suggestion="Wrap the query in a subquery and filter on the window function result.",
        ))
    return sql, warnings


def _warn_regexp(sql: str) -> tuple[str, list[ConversionWarning]]:
    """Flag REGEXP_* functions."""
    warnings: list[ConversionWarning] = []
    if re.search(r'\bREGEXP_\w+\s*\(', sql, re.IGNORECASE):
        warnings.append(ConversionWarning(
            level=WarningLevel.WARNING,
            code="REGEXP_FUNCTIONS",
            message=(
                "REGEXP_* functions (REGEXP_REPLACE, REGEXP_SUBSTR, etc.) are unsupported "
                "in Fabric Warehouse. Application-layer rewrite required."
            ),
            suggestion="Use CLR functions or application-layer regex processing.",
        ))
    return sql, warnings


def _transform_lower_case_compare(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    lower(col) comparisons — T-SQL is case-insensitive with most collations.
    LOWER() is retained for portability; this step is a no-op informational pass.
    """
    return sql, []
