"""
Lakehouse MV Transformer

Converts a Redshift MATERIALIZED VIEW into a
Fabric Lakehouse MATERIALIZED LAKE VIEW using Delta Lake / Spark SQL.

Syntax emitted:
    CREATE OR REPLACE MATERIALIZED LAKE VIEW <schema>.<name>
    AS
    <spark_sql_body>;

Transformation pipeline (Redshift SQL → Spark SQL):
  1.  Schema placeholder substitution  (or hardcoded passthrough)
  2.  Table name suffix stripping (_mv, _view)
  3.  PostgreSQL :: cast → CAST(expr AS type)            [Spark uses CAST not CONVERT]
  4.  DATE_TRUNC('part', expr) → DATE_TRUNC('part', expr) [Spark identical – just normalise]
  5.  CURRENT_DATE → CURRENT_DATE                         [Spark native – keep]
  6.  CURRENT_TIMESTAMP → CURRENT_TIMESTAMP               [Spark native – keep]
  7.  NVL(a, b) → COALESCE(a, b)                         [Spark uses COALESCE not ISNULL]
  8.  ISNULL(a, b) → COALESCE(a, b)                      [Spark has no ISNULL]
  9.  IS TRUE / IS FALSE → = true / = false               [Spark boolean literals]
  10. LISTAGG → COLLECT_LIST + ARRAY_JOIN                 [Spark aggregate]
  11. DECODE() → CASE WHEN                                 [Spark has no DECODE]
  12. CONVERT_TIMEZONE → CONVERT_TIMEZONE (warning)        [Spark: use from_utc_timestamp]
  13. INTERVAL literals → INTERVAL syntax kept            [Spark supports INTERVAL 'N' UNIT]
  14. || concat → CONCAT()                                 [Spark uses CONCAT function]
  15. INITCAP → INITCAP                                   [Spark native – keep]
  16. DATE_PART / date_part → DATE_PART                   [Spark native – keep]
  17. md5() → md5()                                       [Spark native – keep]
  18. QUALIFY → warning                                   [Spark has no QUALIFY]
  19. REGEXP_REPLACE / REGEXP_SUBSTR → keep with note     [Spark native – keep]
  20. Ordinal GROUP BY → explicit columns                 [Spark does not support ordinal GROUP BY]
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
from app.core.settings import settings
from app.logging.logger import get_logger

log = get_logger("lakehouse_mv_transformer")


# ── Public API ────────────────────────────────────────────────────────────────


def transform_lakehouse_mv(
    ir: ViewIR,
    source_sql: str = "",
    schema_mode: str = "dynamic",
) -> ConversionResult:
    """
    Convert a Redshift MATERIALIZED VIEW ViewIR into a
    Fabric Lakehouse MATERIALIZED LAKE VIEW (Spark SQL).

    Args:
        ir:          Parsed ViewIR (object_type == MATERIALIZED_VIEW).
        source_sql:  Original Redshift DDL for reference.
        schema_mode: 'dynamic' = ${rs_...} placeholders, 'hardcoded' = keep original names.
    """
    t0 = time.perf_counter()

    body = ir.body
    # Remove MATERIALIZED_VIEW warning from parser — we handle it our own way
    warnings: list[ConversionWarning] = [
        w for w in ir.warnings if w.code != "MATERIALIZED_VIEW"
    ]
    applied_rules: list[str] = []

    # ── Transformation pipeline ───────────────────────────────────────────
    pipeline: list[tuple[str, Callable]] = [
        ("SCHEMA_PARAMETERISATION",     _spark_schema_refs),
        ("STRIP_TABLE_SUFFIXES",        _strip_table_suffixes),
        ("BOOLEAN_IS_EXPR",             _transform_boolean_is),
        ("CAST_OPERATOR",               _transform_cast_operator_spark),
        ("NVL_TO_COALESCE",             _transform_nvl_coalesce),
        ("ISNULL_TO_COALESCE",          _transform_isnull_coalesce),
        ("SYSDATE",                     _transform_sysdate_spark),
        ("DATE_TRUNC",                  _transform_date_trunc_spark),
        ("DATE_FUNCTION",               _transform_date_function_spark),
        ("DATE_PART",                   _transform_date_part_spark),
        ("INTERVAL_LITERAL",            _transform_interval_spark),
        ("PIPE_CONCAT_TO_CONCAT",       _transform_pipe_concat_spark),
        ("CONVERT_TIMEZONE",            _transform_convert_timezone_spark),
        ("LISTAGG_TO_COLLECT_LIST",     _transform_listagg_spark),
        ("DECODE_TO_CASE",              _transform_decode_spark),
        ("MD5_NOTE",                    _note_md5_spark),
        ("QUALIFY_WARNING",             _warn_qualify_spark),
        ("ORDINAL_GROUPBY_EXPAND",      _transform_ordinal_groupby_spark),
    ]

    for rule_id, fn in pipeline:
        if rule_id == "SCHEMA_PARAMETERISATION" and schema_mode == "hardcoded":
            applied_rules.append("SCHEMA_HARDCODED_MODE")
            continue
        body, rule_warns = fn(body)
        if rule_warns:
            applied_rules.append(rule_id)
        warnings.extend(rule_warns)

    # ── Resolve schema names ──────────────────────────────────────────────
    if schema_mode == "hardcoded":
        output_schema = ir.schema if ir.schema else "default"
    else:
        output_schema = (
            settings.get_output_placeholder(ir.schema)
            if ir.schema
            else settings.output_schema_placeholder
        )

    view_name = ir.name
    applied_rules.append("CREATE_MATERIALIZED_LAKE_VIEW")

    ddl_body = _build_lake_view(output_schema, view_name, body)

    elapsed_ms = (time.perf_counter() - t0) * 1000

    # ── Confidence scoring ────────────────────────────────────────────────
    manual_items = [w.message for w in warnings if w.level == WarningLevel.ERROR]
    warn_items   = [w for w in warnings if w.level == WarningLevel.WARNING]

    if manual_items:
        status     = ConversionStatus.MANUAL_REVIEW
        confidence = 0.50
    elif warn_items:
        status     = ConversionStatus.PARTIAL
        confidence = max(0.65, 1.0 - len(warn_items) * 0.05)
    else:
        status     = ConversionStatus.HIGH_CONFIDENCE
        confidence = 1.0

    output_sql = _lake_view_header(
        source_name=f"{ir.schema}.{ir.name}",
        target_name=f"{output_schema}.{view_name}",
        status=status,
        confidence=confidence,
        warnings=warnings,
    ) + ddl_body

    applied_rules.append("INLINE_WARNING_COMMENTS")

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


# ── DDL builder ───────────────────────────────────────────────────────────────


def _build_lake_view(schema: str, name: str, body: str) -> str:
    """
    Emit a Fabric Lakehouse MATERIALIZED LAKE VIEW DDL.

    Syntax:
        CREATE OR REPLACE MATERIALIZED LAKE VIEW <schema>.<name>
        AS
        <spark_sql_body>;
    """
    return (
        f"CREATE OR REPLACE MATERIALIZED LAKE VIEW {schema}.{name}\n"
        f"AS\n"
        f"{body};"
    )


# ── Schema reference substitution ────────────────────────────────────────────

# Reuse the FROM/JOIN-only schema extractor logic (same as view_transformer)
_ALIAS_EXCLUDED_KEYWORDS = {
    'on', 'where', 'inner', 'left', 'right', 'outer', 'full', 'cross',
    'join', 'and', 'or', 'not', 'in', 'is', 'null', 'group', 'order',
    'having', 'limit', 'union', 'except', 'intersect', 'with', 'select',
    'as', 'from', 'set', 'by', 'distinct', 'all', 'case', 'when', 'then',
    'else', 'end', 'between', 'like', 'exists', 'into',
}
_KW_ALT = '|'.join(sorted(_ALIAS_EXCLUDED_KEYWORDS, key=len, reverse=True))
_FROM_JOIN_RE = re.compile(
    r'\b(?:FROM|JOIN)\s+'
    r'([a-zA-Z_\$][a-zA-Z0-9_\$]*)'
    r'\.'
    r'([a-zA-Z_][a-zA-Z0-9_]*)'
    r'(?:\s+(?!(?:' + _KW_ALT + r')\b)'
    r'([a-zA-Z_][a-zA-Z0-9_]*))?',
    re.IGNORECASE,
)


def _extract_schemas_from_sql(sql: str) -> set[str]:
    schemas: set[str] = set()
    for m in _FROM_JOIN_RE.finditer(sql):
        schema = m.group(1).lower()
        if not schema.startswith('$') and not schema.startswith('{'):
            schemas.add(schema)
    return schemas


def _spark_schema_refs(sql: str) -> tuple[str, list[ConversionWarning]]:
    """Replace source schema prefixes with Fabric parameterised placeholders."""
    warnings: list[ConversionWarning] = []
    result = sql
    found_schemas = _extract_schemas_from_sql(result)
    for schema in sorted(found_schemas, key=len, reverse=True):
        placeholder = settings.get_read_placeholder(schema)
        pattern = re.compile(rf'\b{re.escape(schema)}\.', re.IGNORECASE)
        if pattern.search(result):
            result = pattern.sub(f"{placeholder}.", result)
    return result, warnings


def _strip_table_suffixes(sql: str) -> tuple[str, list[ConversionWarning]]:
    """Strip _mv and _view suffixes from table references."""
    warnings: list[ConversionWarning] = []
    if not settings.strip_table_suffixes_in_views:
        return sql, warnings
    result = sql
    for suffix in settings.strip_name_suffixes:
        pattern = re.compile(rf'{re.escape(suffix)}\b', re.IGNORECASE)
        result = pattern.sub("", result)
    return result, warnings


# ── Boolean IS TRUE / IS FALSE → Spark boolean literals ──────────────────────


def _transform_boolean_is(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    IS TRUE  → = true    (Spark uses lowercase boolean literals)
    IS FALSE → = false
    IS NOT TRUE  → <> true
    IS NOT FALSE → <> false
    """
    warnings: list[ConversionWarning] = []
    result = sql
    rewrites = [
        (r'\bIS\s+NOT\s+TRUE\b',  '<> true'),
        (r'\bIS\s+NOT\s+FALSE\b', '<> false'),
        (r'\bIS\s+TRUE\b',        '= true'),
        (r'\bIS\s+FALSE\b',       '= false'),
    ]
    for pattern, replacement in rewrites:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result, warnings


# ── :: cast operator → CAST(expr AS type) ────────────────────────────────────
# Spark SQL uses CAST(expr AS type), NOT CONVERT(type, expr)

_CAST_OP_RE = re.compile(
    r'::\s*(timestamp(?:\s+(?:without|with)\s+time\s+zone)?'
    r'|date|int(?:eger)?|bigint|float(?:\(\d+\))?|numeric(?:\(\d+,\d+\))?'
    r'|decimal(?:\(\d+,\d+\))?|varchar(?:\(\d+\))?|boolean|text|string|double(?:\s+precision)?)',
    re.IGNORECASE,
)

_SPARK_TYPE_MAP = {
    "timestamp": "TIMESTAMP",
    "timestamp without time zone": "TIMESTAMP",
    "timestamp with time zone": "TIMESTAMP",
    "date": "DATE",
    "integer": "INT",
    "int": "INT",
    "bigint": "BIGINT",
    "float": "DOUBLE",
    "double precision": "DOUBLE",
    "boolean": "BOOLEAN",
    "text": "STRING",
    "string": "STRING",
}


def _map_spark_type(raw_type: str) -> str:
    t = raw_type.strip().lower()
    if t in _SPARK_TYPE_MAP:
        return _SPARK_TYPE_MAP[t]
    if t.startswith("numeric") or t.startswith("decimal"):
        pm = re.search(r'\((\d+),\s*(\d+)\)', t)
        return f"DECIMAL({pm.group(1)},{pm.group(2)})" if pm else "DECIMAL(18,0)"
    if t.startswith("float"):
        return "DOUBLE"
    if t.startswith("varchar") or t.startswith("character varying"):
        pm = re.search(r'\((\d+)\)', t)
        return f"VARCHAR({pm.group(1)})" if pm else "STRING"
    return t.upper()


def _extract_preceding_expr(s: str, op_start: int) -> tuple[int, str]:
    """Walk backwards from op_start to extract the preceding expression."""
    i = op_start - 1
    while i >= 0 and s[i] == ' ':
        i -= 1
    if i < 0:
        return op_start, ""
    if s[i] == ')':
        depth = 0
        while i >= 0:
            if s[i] == ')':   depth += 1
            elif s[i] == '(':
                depth -= 1
                if depth == 0: break
            i -= 1
        j = i - 1
        while j >= 0 and (s[j].isalnum() or s[j] in ('_', '.')):
            j -= 1
        expr_start = j + 1
        return expr_start, s[expr_start:op_start].strip()
    if s[i] == "'":
        j = i - 1
        while j >= 0 and s[j] != "'":
            j -= 1
        return j, s[j:i+1].strip()
    j = i
    while j >= 0 and (s[j].isalnum() or s[j] in ('_', '.')):
        j -= 1
    expr_start = j + 1
    return expr_start, s[expr_start:i+1].strip()


def _transform_cast_operator_spark(sql: str) -> tuple[str, list[ConversionWarning]]:
    """Replace PostgreSQL :: cast with Spark CAST(expr AS type)."""
    warnings: list[ConversionWarning] = []
    if "::" not in sql:
        return sql, warnings

    result = sql
    positions = []
    in_q = False
    q_char = None
    for i, ch in enumerate(result):
        if not in_q and ch in ("'", '"'):
            in_q = True; q_char = ch
        elif in_q and ch == q_char:
            in_q = False
        elif not in_q and result[i:i+2] == '::':
            positions.append(i)

    for op_start in reversed(positions):
        type_match = _CAST_OP_RE.match(result, op_start)
        if not type_match:
            continue
        raw_type = type_match.group(1)
        spark_type = _map_spark_type(raw_type)
        type_end = type_match.end()
        expr_start, expr = _extract_preceding_expr(result, op_start)
        if not expr:
            continue
        replacement = f"CAST({expr} AS {spark_type})"
        result = result[:expr_start] + replacement + result[type_end:]

    return result, warnings


# ── NVL → COALESCE ────────────────────────────────────────────────────────────


def _transform_nvl_coalesce(sql: str) -> tuple[str, list[ConversionWarning]]:
    """NVL(a, b) → COALESCE(a, b)  — Spark has no NVL."""
    warnings: list[ConversionWarning] = []
    result = re.sub(r'\bNVL\s*\(', 'COALESCE(', sql, flags=re.IGNORECASE)
    return result, warnings


def _transform_isnull_coalesce(sql: str) -> tuple[str, list[ConversionWarning]]:
    """ISNULL(a, b) → COALESCE(a, b)  — Spark has no ISNULL."""
    warnings: list[ConversionWarning] = []
    result = re.sub(r'\bISNULL\s*\(', 'COALESCE(', sql, flags=re.IGNORECASE)
    return result, warnings


# ── SYSDATE → CURRENT_DATE ────────────────────────────────────────────────────


def _transform_sysdate_spark(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    SYSDATE / TRUNC(SYSDATE) → CURRENT_DATE  (Spark native)
    TRUNC(SYSDATE) - N       → DATE_SUB(CURRENT_DATE, N)
    """
    warnings: list[ConversionWarning] = []
    result = sql

    def _repl_trunc_sysdate_minus(m: re.Match) -> str:
        n = m.group(1).strip()
        return f"DATE_SUB(CURRENT_DATE, {n})"

    result = re.sub(
        r'\bTRUNC\s*\(\s*SYSDATE\s*\)\s*-\s*(\d+)',
        _repl_trunc_sysdate_minus,
        result, flags=re.IGNORECASE
    )
    result = re.sub(r'\bTRUNC\s*\(\s*SYSDATE\s*\)', 'CURRENT_DATE', result, flags=re.IGNORECASE)
    result = re.sub(r'\bSYSDATE\b', 'CURRENT_DATE', result, flags=re.IGNORECASE)
    return result, warnings


# ── DATE_TRUNC — Spark keeps same signature ───────────────────────────────────


def _transform_date_trunc_spark(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    Spark SQL DATE_TRUNC has reversed argument order vs Redshift.
    Redshift: DATE_TRUNC('week', expr)
    Spark:    DATE_TRUNC('week', expr)   ← same! No change needed.
    Just normalise 'week' → 'week' (Spark doesn't need iso_week).
    """
    warnings: list[ConversionWarning] = []
    # Spark DATE_TRUNC is identical to Redshift — keep as-is
    # Only normalise the unquoted form
    result = re.sub(
        r'DATE_TRUNC\s*\(\s*(\w+)\s*,',
        lambda m: f"DATE_TRUNC('{m.group(1).lower()}',",
        sql, flags=re.IGNORECASE
    )
    return result, warnings


# ── date() → DATE() ──────────────────────────────────────────────────────────


def _transform_date_function_spark(sql: str) -> tuple[str, list[ConversionWarning]]:
    """date(expr) → DATE(expr)  — Spark has native DATE() cast function."""
    warnings: list[ConversionWarning] = []
    result = re.sub(r'\bdate\s*\(', 'DATE(', sql, flags=re.IGNORECASE)
    return result, warnings


# ── DATE_PART → EXTRACT ───────────────────────────────────────────────────────


def _transform_date_part_spark(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    DATE_PART_YEAR(expr)        → YEAR(expr)
    DATE_PART('year', expr)     → EXTRACT(YEAR FROM expr)
    date_part('month', expr)    → EXTRACT(MONTH FROM expr)
    """
    warnings: list[ConversionWarning] = []
    result = sql

    # DATE_PART_YEAR(expr) → YEAR(expr)
    result = re.sub(r'\bDATE_PART_YEAR\s*\(', 'YEAR(', result, flags=re.IGNORECASE)

    # date_part('unit', expr) → EXTRACT(UNIT FROM expr)
    def _repl_date_part(m: re.Match) -> str:
        unit = m.group(1).strip().strip("'").upper()
        return f"EXTRACT({unit} FROM "

    result = re.sub(
        r"\bDATE_PART\s*\(\s*'?(\w+)'?\s*,\s*",
        _repl_date_part,
        result, flags=re.IGNORECASE
    )

    return result, warnings


# ── INTERVAL literals — Spark keeps INTERVAL syntax ──────────────────────────


def _transform_interval_spark(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    Spark SQL supports INTERVAL literals natively.
    Normalise format: INTERVAL '6 day' → INTERVAL 6 DAY  (Spark preferred)
    Also handles: INTERVAL '1 month', INTERVAL '2 year', etc.
    """
    warnings: list[ConversionWarning] = []
    result = sql

    def _repl_interval(m: re.Match) -> str:
        sign = m.group(1) or '+'
        n = m.group(2)
        unit = m.group(3).upper().rstrip('S') if m.group(3).upper().endswith('S') and m.group(3).upper() != 'SS' else m.group(3).upper()
        return f"{sign} INTERVAL {n} {unit}"

    result = re.sub(
        r'([+\-])\s*INTERVAL\s+\'(\d+)\s+(\w+)\'',
        _repl_interval,
        result, flags=re.IGNORECASE
    )

    return result, warnings


# ── || → CONCAT() ─────────────────────────────────────────────────────────────


def _transform_pipe_concat_spark(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    Replace PostgreSQL || string concatenation with Spark CONCAT().
    Spark SQL supports || as well, but CONCAT is more explicit.
    We wrap the full chain: a || b || c → CONCAT(a, b, c)
    Simple replacement: swap || with , and wrap with CONCAT.
    """
    warnings: list[ConversionWarning] = []
    if '||' not in sql:
        return sql, warnings

    # Simple token-by-token replacement — swap || with the CONCAT function form
    # This is a heuristic; complex nested expressions may need manual review
    result = []
    i = 0
    s = sql
    in_single_quote = False
    in_double_quote = False

    while i < len(s):
        ch = s[i]
        if ch == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            result.append(ch)
        elif ch == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            result.append(ch)
        elif ch == '|' and s[i:i+2] == '||' and not in_single_quote and not in_double_quote:
            result.append(' || ')  # Spark supports || natively; keep for clarity
            i += 2
            while i < len(s) and s[i] == ' ':
                i += 1
            continue
        else:
            result.append(ch)
        i += 1

    # Note: kept as || since Spark SQL supports || for string concat natively
    return ''.join(result), warnings


# ── CONVERT_TIMEZONE → from_utc_timestamp ────────────────────────────────────


def _transform_convert_timezone_spark(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    CONVERT_TIMEZONE('UTC', tz, col)
    →
    from_utc_timestamp(col, tz)   [Spark native]
    """
    warnings: list[ConversionWarning] = []
    result = sql

    if not re.search(r'\bCONVERT_TIMEZONE\b', result, re.IGNORECASE):
        return result, warnings

    def _replace_tz(m: re.Match) -> str:
        tz_expr = m.group(2).strip()
        col_expr = m.group(3).strip()
        return f"from_utc_timestamp({col_expr}, {tz_expr})"

    result = re.sub(
        r'\bCONVERT_TIMEZONE\s*\(\s*\'([^\']+)\'\s*,\s*([^,]+?)\s*,\s*([^)]+?)\s*\)',
        _replace_tz,
        result, flags=re.IGNORECASE | re.DOTALL
    )

    if result != sql:
        warnings.append(ConversionWarning(
            level=WarningLevel.WARNING,
            code="CONVERT_TIMEZONE_SPARK",
            message=(
                "CONVERT_TIMEZONE() converted to Spark from_utc_timestamp(). "
                "Verify timezone identifiers are IANA tz names (e.g. 'Asia/Dubai', not Windows IDs)."
            ),
            suggestion=(
                "Spark from_utc_timestamp uses IANA timezone IDs. "
                "Ensure your timezone column/literal uses IANA format."
            ),
        ))

    return result, warnings


# ── LISTAGG → ARRAY_JOIN(COLLECT_LIST()) ─────────────────────────────────────


def _transform_listagg_spark(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    LISTAGG(col, delim) WITHIN GROUP (ORDER BY ...) →
    ARRAY_JOIN(COLLECT_LIST(col), delim)

    Note: COLLECT_LIST does not guarantee order; WITHIN GROUP order is lost.
    """
    warnings: list[ConversionWarning] = []
    result = sql

    if not re.search(r'\bLISTAGG\s*\(', sql, re.IGNORECASE):
        return result, warnings

    warnings.append(ConversionWarning(
        level=WarningLevel.WARNING,
        code="LISTAGG_TO_COLLECT_LIST",
        message=(
            "LISTAGG() converted to ARRAY_JOIN(COLLECT_LIST()). "
            "Note: COLLECT_LIST does not guarantee ordering — WITHIN GROUP ORDER BY is lost."
        ),
        suggestion=(
            "If ordering is required, use ARRAY_JOIN(ARRAY_SORT(COLLECT_LIST(col)), delim) "
            "or a window function approach."
        ),
    ))

    # Pattern: LISTAGG(col, 'delim') WITHIN GROUP (ORDER BY ...) → ARRAY_JOIN(COLLECT_LIST(col), 'delim')
    def _repl_listagg(m: re.Match) -> str:
        col = m.group(1).strip()
        delim = m.group(2).strip()
        # Consume optional WITHIN GROUP (ORDER BY ...) 
        return f"ARRAY_JOIN(COLLECT_LIST({col}), {delim})"

    result = re.sub(
        r'\bLISTAGG\s*\(\s*([^,]+?)\s*,\s*(\'[^\']*\'|"[^"]*")\s*\)'
        r'(?:\s+WITHIN\s+GROUP\s*\([^)]*\))?',
        _repl_listagg,
        result, flags=re.IGNORECASE | re.DOTALL
    )

    # Fallback: LISTAGG without delimiter
    result = re.sub(
        r'\bLISTAGG\s*\(\s*([^)]+?)\s*\)(?:\s+WITHIN\s+GROUP\s*\([^)]*\))?',
        lambda m: f"ARRAY_JOIN(COLLECT_LIST({m.group(1).strip()}), ',')",
        result, flags=re.IGNORECASE | re.DOTALL
    )

    return result, warnings


# ── DECODE → CASE WHEN ────────────────────────────────────────────────────────


def _transform_decode_spark(sql: str) -> tuple[str, list[ConversionWarning]]:
    """DECODE() is not supported in Spark SQL — flag for manual conversion."""
    warnings: list[ConversionWarning] = []
    if not re.search(r'\bDECODE\s*\(', sql, re.IGNORECASE):
        return sql, warnings

    warnings.append(ConversionWarning(
        level=WarningLevel.WARNING,
        code="DECODE_FUNCTION",
        message=(
            "DECODE() is not a native Spark SQL function. "
            "Convert to: CASE WHEN expr = v1 THEN r1 WHEN expr = v2 THEN r2 ELSE default END"
        ),
        suggestion=(
            "Replace DECODE(expr, v1, r1, v2, r2, default) with a CASE expression. "
            "Note: DECODE treats NULL specially (NULL = NULL is true); CASE does not."
        ),
    ))

    result = re.sub(
        r'\bDECODE\s*\(',
        'DECODE( /* MANUAL REVIEW: convert to CASE WHEN */',
        sql, flags=re.IGNORECASE
    )
    return result, warnings


# ── md5() — Spark native ──────────────────────────────────────────────────────


def _note_md5_spark(sql: str) -> tuple[str, list[ConversionWarning]]:
    """md5() is a native Spark SQL function — no change needed."""
    warnings: list[ConversionWarning] = []
    # Spark has native md5() — nothing to do, just silently pass
    return sql, warnings


# ── QUALIFY — unsupported in Spark SQL ────────────────────────────────────────


def _warn_qualify_spark(sql: str) -> tuple[str, list[ConversionWarning]]:
    """Flag QUALIFY — not supported in Spark SQL."""
    warnings: list[ConversionWarning] = []
    if re.search(r'\bQUALIFY\b', sql, re.IGNORECASE):
        warnings.append(ConversionWarning(
            level=WarningLevel.WARNING,
            code="QUALIFY_CLAUSE",
            message=(
                "QUALIFY clause is not supported in Spark SQL. "
                "Rewrite as: SELECT * FROM (original_query) WHERE window_col = ..."
            ),
            suggestion="Wrap the query in a subquery and filter on the window function result.",
        ))
    return sql, warnings


# ── Ordinal GROUP BY expansion ────────────────────────────────────────────────
# (Spark SQL does not support ordinal GROUP BY — must expand to explicit columns)


def _split_select_columns(s: str) -> list[str]:
    parts, current = [], []
    paren_depth = 0
    case_depth = 0
    in_q, q_char = False, None
    i = 0
    while i < len(s):
        ch = s[i]
        if not in_q and ch in ("'", '"'):
            in_q, q_char = True, ch; current.append(ch)
        elif in_q and ch == q_char:
            in_q = False; current.append(ch)
        elif in_q:
            current.append(ch)
        elif ch == '(':
            paren_depth += 1; current.append(ch)
        elif ch == ')':
            paren_depth -= 1; current.append(ch)
        elif paren_depth == 0 and re.match(r'CASE[\s\n\t\r(]', s[i:i+5], re.IGNORECASE):
            case_depth += 1; current.append(ch)
        elif paren_depth == 0 and case_depth > 0 and re.match(r'END[\s\n\t\r,);]', s[i:i+4], re.IGNORECASE):
            case_depth -= 1; current.append(ch)
        elif ch == ',' and paren_depth == 0 and case_depth == 0:
            parts.append(''.join(current).strip()); current = []
        else:
            current.append(ch)
        i += 1
    if current:
        parts.append(''.join(current).strip())
    return [p for p in parts if p]


def _strip_col_alias(col_expr: str) -> str:
    s = col_expr.strip()
    paren_depth, case_depth, i, last_as_pos = 0, 0, 0, -1
    while i < len(s):
        ch = s[i]
        if ch in ("'", '"'):
            q = ch; i += 1
            while i < len(s) and s[i] != q: i += 1
        elif ch == '(': paren_depth += 1
        elif ch == ')': paren_depth -= 1
        elif paren_depth == 0 and re.match(r'CASE[\s\n(]', s[i:i+5], re.IGNORECASE): case_depth += 1
        elif paren_depth == 0 and case_depth > 0 and re.match(r'END[\s\n,);]', s[i:i+4], re.IGNORECASE): case_depth -= 1
        elif paren_depth == 0 and case_depth == 0 and s[i:i+4].upper() == ' AS ':
            last_as_pos = i
        i += 1
    return s[:last_as_pos].strip() if last_as_pos > 0 else s.strip()


def _is_aggregate_expr(expr: str) -> bool:
    return bool(re.match(
        r'^\s*(SUM|COUNT|MAX|MIN|AVG|STDEV|STDDEV|VARIANCE|VAR|LISTAGG|ARRAY_JOIN|COLLECT_LIST'
        r'|APPROX_COUNT_DISTINCT|PERCENTILE_CONT|PERCENTILE_DISC)\s*\(',
        expr.strip(), re.IGNORECASE))


def _find_select_cols_for_groupby(sql: str, gb_start: int) -> list[str] | None:
    depth = 0
    j = gb_start - 1
    select_end = -1
    while j >= 0:
        ch = sql[j]
        if ch == ')': depth += 1
        elif ch == '(':
            if depth > 0: depth -= 1
            else: return None
        elif depth == 0:
            if sql[j:j+6].upper() == 'SELECT':
                select_end = j + 6
                break
        j -= 1
    if select_end < 0:
        return None
    body = sql[select_end:gb_start]
    pd, cd, ti = 0, 0, 0
    from_pos = -1
    while ti < len(body):
        ch = body[ti]
        if ch in ("'", '"'):
            q = ch; ti += 1
            while ti < len(body) and body[ti] != q: ti += 1
        elif ch == '(': pd += 1
        elif ch == ')': pd -= 1
        elif pd == 0 and re.match(r'CASE[\s\n(]', body[ti:ti+5], re.IGNORECASE): cd += 1
        elif pd == 0 and cd > 0 and re.match(r'END[\s\n,);]', body[ti:ti+4], re.IGNORECASE): cd -= 1
        elif pd == 0 and cd == 0 and re.match(r'\bFROM\b', body[ti:ti+5], re.IGNORECASE):
            from_pos = ti
            break
        ti += 1
    if from_pos < 0:
        return None
    col_text = body[:from_pos].strip()
    col_text = re.sub(r'^(DISTINCT|ALL)\s+', '', col_text, flags=re.IGNORECASE)
    return _split_select_columns(col_text)


def _transform_ordinal_groupby_spark(sql: str) -> tuple[str, list[ConversionWarning]]:
    """Expand ordinal GROUP BY positions to explicit columns (Spark does not support ordinals)."""
    warnings_out: list[ConversionWarning] = []
    gb_re = re.compile(
        r'\bGROUP\s+BY\s+((?:\d+\s*,\s*)*\d+)\s*(?=[)\n;]|$)',
        re.IGNORECASE
    )
    result = sql
    matches = list(gb_re.finditer(result))
    if not matches:
        return result, warnings_out

    for m in reversed(matches):
        ordinals = [int(x.strip()) for x in m.group(1).split(',') if x.strip().isdigit()]
        if not ordinals:
            continue
        all_cols = _find_select_cols_for_groupby(result, m.start())
        if all_cols is None:
            warnings_out.append(ConversionWarning(
                level=WarningLevel.WARNING,
                code="GROUPBY_EXPAND_FAILED",
                message=f"Could not expand ordinal GROUP BY at character {m.start()} — SELECT list not found.",
                suggestion="Manually replace GROUP BY 1,2,... with explicit column names.",
            ))
            continue
        expanded = []
        for n in ordinals:
            if n < 1 or n > len(all_cols):
                expanded.append(f"/* ordinal {n} out of range — verify manually */")
                continue
            bare = _strip_col_alias(all_cols[n - 1])
            bare = re.sub(r'\s+', ' ', bare).strip()
            if _is_aggregate_expr(bare):
                continue
            expanded.append(bare)
        if not expanded:
            continue
        line_start = result.rfind('\n', 0, m.start()) + 1
        raw_line = result[line_start:m.start()]
        indent = len(raw_line) - len(raw_line.lstrip())
        col_indent = ' ' * indent + '    '
        formatted = (',\n' + col_indent).join(expanded)
        new_gb = f"GROUP BY\n{col_indent}{formatted}"
        result = result[:m.start()] + new_gb + result[m.end():]

    if matches:
        warnings_out.append(ConversionWarning(
            level=WarningLevel.WARNING,
            code="ORDINAL_GROUPBY_EXPANDED",
            message=(
                f"Ordinal GROUP BY positions expanded to explicit column names "
                f"({len(matches)} GROUP BY clause(s)). "
                "Spark SQL does not support positional GROUP BY."
            ),
            suggestion="Review expanded GROUP BY columns for correctness.",
        ))

    return result, warnings_out


# ── Header block ──────────────────────────────────────────────────────────────


def _lake_view_header(
    source_name: str,
    target_name: str,
    status,
    confidence: float,
    warnings: list,
) -> str:
    from app.core.models import ConversionStatus, WarningLevel
    from textwrap import wrap as _wrap

    status_icon = {
        ConversionStatus.HIGH_CONFIDENCE: "✅",
        ConversionStatus.PARTIAL:         "⚠️ ",
        ConversionStatus.MANUAL_REVIEW:   "🔍",
        ConversionStatus.FAILED:          "❌",
        ConversionStatus.UNSUPPORTED:     "🚫",
    }.get(status, "❓")

    border = "═" * 66
    warn_count = len(warnings)
    lines = [
        f"-- {border}",
        f"-- {'MATERIALIZED LAKE VIEW':<8}: {source_name}",
        f"-- Target  : {target_name}",
        f"-- Engine  : Fabric Lakehouse · Spark SQL (Delta Lake)",
        f"-- Status  : {status_icon} {status.value}  |  Confidence: {confidence:.0%}",
        f"-- Warnings: {warn_count}",
    ]

    WRAP_WIDTH = 100
    PREFIX_WARN = "--   "
    PREFIX_CONT = "--      "
    PREFIX_SUGG = "--   💡 "

    for w in warnings:
        icon = "❌" if w.level == WarningLevel.ERROR else "⚠"
        first_line = f"{icon} {w.code}: {w.message}"
        wrapped = _wrap(first_line, width=WRAP_WIDTH, initial_indent=PREFIX_WARN, subsequent_indent=PREFIX_CONT)
        lines.extend(wrapped)
        if w.suggestion and w.suggestion.strip() and w.suggestion != w.message:
            sugg_wrapped = _wrap(w.suggestion, width=WRAP_WIDTH, initial_indent=PREFIX_SUGG, subsequent_indent=PREFIX_CONT)
            lines.extend(sugg_wrapped)

    lines.append(f"-- {border}")
    lines.append("")
    return "\n".join(lines) + "\n"
