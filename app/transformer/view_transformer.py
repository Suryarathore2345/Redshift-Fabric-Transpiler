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


def transform_view(ir: ViewIR, source_sql: str = "", schema_mode: str = "dynamic") -> ConversionResult:
    """
    Apply all transformation rules to a ViewIR and produce a ConversionResult.

    Args:
        ir:          Parsed ViewIR.
        source_sql:  Original raw SQL.
        schema_mode: 'dynamic' = parameterised ${...} placeholders (default),
                     'hardcoded' = keep original schema names as-is.
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
        # ── CAST operator must run BEFORE NVL so nvl(x)::timestamp
        # is first cleaned into a proper cast, then NVL→ISNULL runs
        ("CAST_OPERATOR",             _transform_cast_operator),
        # ── NVL/COALESCE smart conversion (arg-count aware)
        ("NVL_TO_ISNULL_OR_COALESCE", _transform_nvl),
        ("COALESCE_2ARG_TO_ISNULL",   _transform_coalesce_2arg),
        # ── CAST→CONVERT preference
        ("CAST_TO_CONVERT",           _transform_cast_to_convert),
        # ── Date/time
        ("DATE_PART_YEAR",            _transform_date_part_year),
        ("DATE_TRUNC",                _transform_date_trunc),
        ("DATE_FUNCTION",             _transform_date_function),
        ("CURRENT_DATE",              _transform_current_date),
        ("CURRENT_TIMESTAMP",         _transform_current_timestamp),
        ("SYSDATE_TRUNC",             _transform_sysdate_trunc),
        ("INTERVAL_LITERAL",          _transform_interval),
        # ── Pipe concat → + (must run after date transforms)
        ("PIPE_CONCAT_TO_PLUS",       _transform_pipe_concat),
        # ── Timezone
        ("CONVERT_TIMEZONE",          _transform_convert_timezone),
        # ── String functions
        ("INITCAP_TO_UPPER",          _transform_initcap),
        # ── Aggregation
        ("LISTAGG_TO_STRING_AGG",     _transform_listagg),
        ("DECODE_TO_CASE",            _transform_decode),
        # ── Warnings
        ("MD5_WARNING",               _warn_md5),
        ("QUALIFY_WARNING",           _warn_qualify),
        ("REGEXP_WARNING",            _warn_regexp),
        ("LOWER_FILTER",              _transform_lower_case_compare),
        # ── GROUP BY ordinal expansion (must run last — after all other transforms)
        ("ORDINAL_GROUPBY_EXPAND",    _transform_ordinal_groupby),
    ]

    for rule_id, fn in pipeline:
        # Skip schema parameterisation in hardcoded mode
        if rule_id == "SCHEMA_PARAMETERISATION" and schema_mode == "hardcoded":
            applied_rules.append("SCHEMA_HARDCODED_MODE")
            continue
        body, rule_warns = fn(body)
        if rule_warns or _rule_changed(body):
            applied_rules.append(rule_id)
        warnings.extend(rule_warns)

    # ── Build CREATE OR ALTER VIEW / stored procedure ─────────────────────
    is_matview = ir.object_type == ObjectType.MATERIALIZED_VIEW
    # Resolve output schema based on schema_mode
    if schema_mode == "hardcoded":
        output_schema = ir.schema if ir.schema else "dbo"
    else:
        output_schema = (
            settings.get_output_placeholder(ir.schema)
            if ir.schema
            else settings.output_schema_placeholder
        )
    view_name = ir.name

    applied_rules.append("CREATE_OR_ALTER_VIEW")

    if is_matview:
        ddl_body = _build_stored_procedure(output_schema, view_name, body, warnings, applied_rules)
    else:
        ddl_body = _build_view(output_schema, view_name, body)

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

    # ── Prepend rich object header with warning summary ───────────────────
    obj_type_label = "MATERIALIZED VIEW -> PROCEDURE" if is_matview else "VIEW"
    output_sql = _view_header(
        obj_type_label=obj_type_label,
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


# ── SQL keywords that cannot be table aliases ─────────────────────────────────
_ALIAS_EXCLUDED_KEYWORDS = {
    'on', 'where', 'inner', 'left', 'right', 'outer', 'full', 'cross',
    'join', 'and', 'or', 'not', 'in', 'is', 'null', 'group', 'order',
    'having', 'limit', 'union', 'except', 'intersect', 'with', 'select',
    'as', 'from', 'set', 'by', 'distinct', 'all', 'case', 'when', 'then',
    'else', 'end', 'between', 'like', 'exists', 'into',
}

# Compiled once — keyword alternation for the negative lookahead
_KW_ALT = '|'.join(sorted(_ALIAS_EXCLUDED_KEYWORDS, key=len, reverse=True))

# Matches: FROM/JOIN <schema>.<table> [optional_alias]
# The alias group uses a negative lookahead to reject SQL keywords,
# which prevents JOIN/ON/WHERE etc. from being misread as aliases and
# consumed so the next FROM/JOIN token is never found.
_FROM_JOIN_RE = re.compile(
    r'\b(?:FROM|JOIN)\s+'
    r'([a-zA-Z_\$][a-zA-Z0-9_\$]*)' # group 1: schema name
    r'\.' 
    r'([a-zA-Z_][a-zA-Z0-9_]*)' # group 2: table name (not used but required)
    r'(?:\s+(?!(?:' + _KW_ALT + r')\b)' # optional alias: NOT a keyword
    r'([a-zA-Z_][a-zA-Z0-9_]*))?',  # group 3: alias (optional)
    re.IGNORECASE,
)


def _extract_schemas_from_sql(sql: str) -> set[str]:
    """
    Extract true schema names from FROM/JOIN clauses only.

    Only looks at:
        FROM  schema.table [alias]
        JOIN  schema.table [alias]

    This means:
    - Table ALIASES (p, c, cl, ach) used as alias.column in SELECT/WHERE/ON
      are never mistaken for schema names.
    - Already-parameterised placeholders (${rs_...}) are skipped.
    - SQL keywords (JOIN, ON, WHERE ...) cannot be captured as aliases,
      preventing them from masking subsequent FROM/JOIN tokens.
    """
    schemas: set[str] = set()
    for m in _FROM_JOIN_RE.finditer(sql):
        schema = m.group(1).lower()
        if not schema.startswith('$') and not schema.startswith('{'):
            schemas.add(schema)
    return schemas


def _transform_schema_refs(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    Replace source schema prefixes in FROM/JOIN clauses with Fabric
    parameterised placeholders.

    Detection is FROM/JOIN-only — alias.column patterns in SELECT/WHERE/ON
    are never misidentified as schema references.

    Auto-generation (no config needed for new schemas):
      sales.orders    → ${rs_sales}.orders
      master.customer → ${rs_master}.customer
      bi_alefdw.t     → ${rs_bi_alefdw}.t

    Override via settings.schema_placeholder_map_overrides when dev/prod
    should share the same placeholder:
      bi_alefdw_dev → ${rs_bi_alefdw}  (same as prod)
    """
    warnings: list[ConversionWarning] = []
    result = sql

    # Step 1 — collect true schema names (FROM/JOIN only, not aliases)
    found_schemas = _extract_schemas_from_sql(result)

    # Step 2 — replace each, longest name first to avoid partial matches
    # (bi_alefdw_dev must be replaced before bi_alefdw)
    for schema in sorted(found_schemas, key=len, reverse=True):
        placeholder = settings.get_read_placeholder(schema)
        pattern = re.compile(rf'\b{re.escape(schema)}\.', re.IGNORECASE)
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


def _map_cast_type(raw_type: str) -> str:
    """Map a raw Redshift ::type string to a Fabric T-SQL type name."""
    t = raw_type.strip().lower()
    if t in _CAST_TYPE_MAP:
        return _CAST_TYPE_MAP[t]
    if t.startswith("numeric") or t.startswith("decimal"):
        pm = re.search(r"\((\d+),\s*(\d+)\)", t)
        return f"DECIMAL({pm.group(1)},{pm.group(2)})" if pm else "DECIMAL(18,0)"
    if t.startswith("float"):
        pm = re.search(r"\((\d+)\)", t)
        return f"FLOAT({pm.group(1)})" if pm else "FLOAT(53)"
    if t.startswith("varchar") or t.startswith("character varying"):
        pm = re.search(r"\((\d+)\)", t)
        return f"VARCHAR({pm.group(1)})" if pm else "VARCHAR(MAX)"
    return t.upper()


def _extract_preceding_expr(s: str, op_start: int) -> tuple[int, str]:
    """
    Walk BACKWARDS from op_start to extract the full expression that
    precedes a :: operator.

    Handles:
      simple_col::type           → 'simple_col'
      func(args)::type           → 'func(args)'
      func(a,b,c)::type          → 'func(a,b,c)'
      alias.col::type            → 'alias.col'
      (subquery)::type           → '(subquery)'

    Returns (start_index_of_expr, expr_string).
    """
    i = op_start - 1
    # Skip whitespace before ::
    while i >= 0 and s[i] == ' ':
        i -= 1

    if i < 0:
        return op_start, ""

    # Case 1: ends with )  — find matching (
    if s[i] == ')':
        depth = 0
        while i >= 0:
            if s[i] == ')':
                depth += 1
            elif s[i] == '(':
                depth -= 1
                if depth == 0:
                    break
            i -= 1
        expr_end = op_start
        # Also grab the function name before (
        j = i - 1
        while j >= 0 and (s[j].isalnum() or s[j] in ('_', '.')):
            j -= 1
        expr_start = j + 1
        return expr_start, s[expr_start:expr_end].strip()

    # Case 2: ends with ' — string literal
    if s[i] == "'":
        j = i - 1
        while j >= 0 and s[j] != "'":
            j -= 1
        expr_start = j
        return expr_start, s[expr_start:i+1].strip()

    # Case 3: identifier / number (word chars, dots, underscores)
    j = i
    while j >= 0 and (s[j].isalnum() or s[j] in ('_', '.')):
        j -= 1
    expr_start = j + 1
    return expr_start, s[expr_start:i+1].strip()


def _transform_cast_operator(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    Replace PostgreSQL :: cast operator with CONVERT(type, expr).

    Uses a backward-scanning expression extractor to correctly handle:
      simple_col::date                    → CONVERT(DATE, simple_col)
      func(a,b)::timestamp                → CONVERT(DATETIME2(6), func(a,b))
      nvl(a,b)::timestamp                 → CONVERT(DATETIME2(6), nvl(a,b))  [BUG-11 fix]
      alias.col::varchar                  → CONVERT(VARCHAR(MAX), alias.col)
      (fasr.fasr_final_score*0.5+200)::int → CONVERT(INT, (fasr.fasr_final_score*0.5+200))
    """
    warnings: list[ConversionWarning] = []

    if "::" not in sql:
        return sql, warnings

    result = sql
    # Process right-to-left so index positions stay valid
    # Find all :: positions that are not inside string literals
    positions = []
    in_q = False
    q_char = None
    for i, ch in enumerate(result):
        if not in_q and ch in ("'", '"'):
            in_q = True
            q_char = ch
        elif in_q and ch == q_char:
            in_q = False
        elif not in_q and result[i:i+2] == '::':
            positions.append(i)

    # Process in reverse order so replacements don't shift earlier positions
    for op_start in reversed(positions):
        # Extract type after ::
        type_match = _CAST_OP_RE.match(result, op_start)
        if not type_match:
            continue

        raw_type = type_match.group(1)
        fabric_type = _map_cast_type(raw_type)
        type_end = type_match.end()

        # Extract expression before ::
        expr_start, expr = _extract_preceding_expr(result, op_start)
        if not expr:
            continue

        if fabric_type in ("DATE", "DATETIME2(6)"):
            replacement = f"CONVERT({fabric_type}, {expr})"
        else:
            replacement = f"CONVERT({fabric_type}, {expr})"

        result = result[:expr_start] + replacement + result[type_end:]

    return result, warnings


def _count_func_args(sql: str, open_paren_pos: int) -> int:
    """
    Count the number of top-level comma-separated arguments starting at
    open_paren_pos (which points to the character AFTER the opening paren).
    Returns arg count (0 = empty parens, 1 = one arg, 2 = two args, etc.)
    """
    depth = 0
    arg_count = 1
    i = open_paren_pos
    while i < len(sql):
        ch = sql[i]
        if ch in ("'", '"'):
            # Skip quoted string
            quote = ch
            i += 1
            while i < len(sql) and sql[i] != quote:
                if sql[i] == '\\':
                    i += 1
                i += 1
        elif ch == '(':
            depth += 1
        elif ch == ')':
            if depth == 0:
                break
            depth -= 1
        elif ch == ',' and depth == 0:
            arg_count += 1
        i += 1
    return arg_count


def _transform_nvl(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    NVL(a, b)       → ISNULL(a, b)       [2 args — T-SQL ISNULL supports exactly 2]
    NVL(a, b, c...) → COALESCE(a, b, c)  [3+ args — ISNULL does not support 3+ args]
    """
    warnings: list[ConversionWarning] = []
    result = sql

    def _replace_nvl(m: re.Match) -> str:
        # Position of char after '('
        start = m.end()
        arg_count = _count_func_args(result, start)
        return "COALESCE(" if arg_count >= 3 else "ISNULL("

    result = re.sub(r"\bNVL\s*\(", _replace_nvl, result, flags=re.IGNORECASE)
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




# ── COALESCE 2-arg → ISNULL ───────────────────────────────────────────────────


def _transform_coalesce_2arg(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    COALESCE(a, b)       → ISNULL(a, b)   [exactly 2 args]
    COALESCE(a, b, c...) → COALESCE(...)  [3+ args: keep as is]

    ISNULL is the preferred Fabric T-SQL 2-arg null replacement function.
    """
    warnings: list[ConversionWarning] = []
    result = sql

    def _replace_coalesce(m: re.Match) -> str:
        start = m.end()
        arg_count = _count_func_args(result, start)
        return "COALESCE(" if arg_count >= 3 else "ISNULL("

    result = re.sub(r"\bCOALESCE\s*\(", _replace_coalesce, result, flags=re.IGNORECASE)
    return result, warnings


# ── CAST(expr AS type) → CONVERT(type, expr) ─────────────────────────────────


def _transform_cast_to_convert(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    CAST(expr AS type) → CONVERT(type, expr)

    Fabric supports both but CONVERT is the preferred T-SQL form.
    Handles nested CAST, CAST inside CASE WHEN, etc.

    Skips CAST that is already inside CONVERT() to avoid double conversion.
    Only converts standalone CAST(...) not inside another function name
    (e.g. avoids matching TRY_CAST).
    """
    warnings: list[ConversionWarning] = []

    # Match CAST( ... AS type ) where type is a known type keyword
    # We use a balanced-paren approach to find the full CAST(...)
    # Pattern: \bCAST\s*\( — preceded by space/operator/start, not by word char
    cast_re = re.compile(r"(?<![\w])CAST\s*\(", re.IGNORECASE)

    def _replace_cast(m: re.Match, s: str) -> tuple[str, int]:
        """Find the matching ) for this CAST( and rewrite as CONVERT(type, expr)."""
        open_pos = m.end()  # position after (
        # Find the balanced closing paren
        depth = 0
        pos = open_pos
        while pos < len(s):
            ch = s[pos]
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
            elif ch in ("'", '"'):
                quote = ch
                pos += 1
                while pos < len(s) and s[pos] != quote:
                    pos += 1
            pos += 1

        if pos >= len(s):
            return s[m.start():pos + 1], pos + 1  # no match, return unchanged

        inner = s[open_pos:pos]  # content inside CAST(...)
        end_pos = pos + 1        # position after closing )

        # Find the last AS at depth 0 inside inner
        as_pos = -1
        d = 0
        i = 0
        while i < len(inner):
            ch = inner[i]
            if ch == "(":
                d += 1
            elif ch == ")":
                d -= 1
            elif ch in ("'", '"'):
                q = ch
                i += 1
                while i < len(inner) and inner[i] != q:
                    i += 1
            elif d == 0:
                if inner[i:i+4].upper() == " AS " or (inner[i:i+3].upper() == "AS " and i > 0 and inner[i-1] == " "):
                    as_pos = i
            i += 1

        if as_pos < 0:
            # No AS found — cannot safely convert; leave unchanged
            return s[m.start():end_pos], end_pos

        expr = inner[:as_pos].strip()
        type_str = inner[as_pos:].strip()
        # Strip leading AS
        type_str = re.sub(r"^AS\s+", "", type_str, flags=re.IGNORECASE).strip()

        rewritten = f"CONVERT({type_str}, {expr})"
        return rewritten, end_pos

    # Apply iteratively (handles multiple CASTs in a statement)
    result = sql
    while True:
        m = cast_re.search(result)
        if not m:
            break
        replacement, end_pos = _replace_cast(m, result)
        result = result[:m.start()] + replacement + result[end_pos:]

    return result, warnings


# ── DATE_PART_YEAR / date_part(year, ...) → DATEPART(YEAR, ...) ──────────────


def _transform_date_part_year(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    Two Redshift patterns:

    1. DATE_PART_YEAR(expr)              → DATEPART(YEAR, expr)
    2. date_part(year, expr)             → DATEPART(YEAR, expr)
    3. date_part('year', expr)           → DATEPART(YEAR, expr)

    These often appear in || concat chains — the pipe concat transformer
    runs AFTER this one and converts || to +.
    """
    warnings: list[ConversionWarning] = []
    result = sql

    # Pattern 1: DATE_PART_YEAR(expr) → DATEPART(YEAR, expr)
    result = re.sub(
        r"\bDATE_PART_YEAR\s*\(", "DATEPART(YEAR, ",
        result, flags=re.IGNORECASE
    )

    # Pattern 2a: date_part(year, expr) → DATEPART(YEAR, expr)
    result = re.sub(
        r"\bDATE_PART\s*\(\s*year\s*,\s*",
        "DATEPART(YEAR, ",
        result, flags=re.IGNORECASE
    )

    # Pattern 2b: date_part('year', expr) → DATEPART(YEAR, expr)
    result = re.sub(
        r"\bDATE_PART\s*\(\s*\'year\'\s*,\s*",
        "DATEPART(YEAR, ",
        result, flags=re.IGNORECASE
    )

    # Pattern 3: date_part(month/day/hour etc) → DATEPART(...)
    def _repl_date_part(m: re.Match) -> str:
        part = m.group(1).strip().strip("'").upper()
        return f"DATEPART({part}, "

    result = re.sub(
        r"\bDATE_PART\s*\(\s*\'?(\w+)\'?\s*,\s*",
        _repl_date_part,
        result, flags=re.IGNORECASE
    )

    return result, warnings


# ── CONVERT_TIMEZONE → AT TIME ZONE ──────────────────────────────────────────


def _transform_convert_timezone(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    CONVERT_TIMEZONE('UTC', tz_col, ts_col)
    →
    CAST(CAST(ts_col AT TIME ZONE 'UTC' AT TIME ZONE tz_col AS DATETIME2) AS DATE)

    When wrapped in TRUNC() the outer TRUNC is replaced by CAST(... AS DATE).

    Pattern seen in source:
      trunc(convert_timezone('UTC', dsc.tenant_timezone, dsc.school_created_time))
    Correct output:
      CAST(CAST(dsc.school_created_time AT TIME ZONE 'UTC'
               AT TIME ZONE dsc.tenant_timezone AS DATETIME2) AS DATE)
    """
    warnings: list[ConversionWarning] = []
    result = sql

    if not re.search(r"\bCONVERT_TIMEZONE\b", result, re.IGNORECASE):
        return result, warnings

    # Pattern: TRUNC(CONVERT_TIMEZONE('UTC', tz, col))
    def _replace_trunc_tz(m: re.Match) -> str:
        src_utc = m.group(1).strip().strip("'")  # 'UTC' → UTC
        tz_expr = m.group(2).strip()
        col_expr = m.group(3).strip()
        return (
            f"CAST(CAST({col_expr} AT TIME ZONE 'UTC' "
            f"AT TIME ZONE {tz_expr} AS DATETIME2) AS DATE)"
        )

    result = re.sub(
        r"\bTRUNC\s*\(\s*CONVERT_TIMEZONE\s*\("
        r"\s*\'([^\']+)\'\s*,"
        r"\s*([^,]+?)\s*,"
        r"\s*([^)]+?)\s*\)\s*\)",
        _replace_trunc_tz,
        result, flags=re.IGNORECASE | re.DOTALL
    )

    # Pattern: CONVERT_TIMEZONE('UTC', tz, col) without TRUNC
    def _replace_tz(m: re.Match) -> str:
        tz_expr = m.group(2).strip()
        col_expr = m.group(3).strip()
        return (
            f"CAST({col_expr} AT TIME ZONE 'UTC' "
            f"AT TIME ZONE {tz_expr} AS DATETIME2)"
        )

    result = re.sub(
        r"\bCONVERT_TIMEZONE\s*\("
        r"\s*\'([^\']+)\'\s*,"
        r"\s*([^,]+?)\s*,"
        r"\s*([^)]+?)\s*\)",
        _replace_tz,
        result, flags=re.IGNORECASE | re.DOTALL
    )

    if result != sql:
        warnings.append(ConversionWarning(
            level=WarningLevel.WARNING,
            code="CONVERT_TIMEZONE",
            message=(
                "CONVERT_TIMEZONE() converted to AT TIME ZONE pattern. "
                "Verify timezone identifiers are Windows-style TZ names "
                "(e.g. 'Arabian Standard Time' not 'Asia/Dubai')."
            ),
            suggestion=(
                "Fabric T-SQL AT TIME ZONE requires Windows timezone IDs. "
                "Replace IANA tz names (e.g. 'Asia/Dubai') with Windows equivalents."
            ),
        ))

    return result, warnings


# ── TRUNC(SYSDATE) - N → DATEADD(DAY, -N, CAST(GETDATE() AS DATE)) ──────────


def _transform_sysdate_trunc(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    Trunc(sysdate) - 1   → DATEADD(DAY, -1, CAST(GETDATE() AS DATE))
    Trunc(sysdate) - N   → DATEADD(DAY, -N, CAST(GETDATE() AS DATE))
    Trunc(sysdate)       → CAST(GETDATE() AS DATE)

    Must run AFTER _transform_convert_timezone so that
    trunc(convert_timezone(...)) is already handled.
    """
    warnings: list[ConversionWarning] = []
    result = sql

    # trunc(sysdate) - N
    def _repl_trunc_sysdate_minus(m: re.Match) -> str:
        n = m.group(1).strip()
        return f"DATEADD(DAY, -{n}, CAST(GETDATE() AS DATE))"

    result = re.sub(
        r"\bTRUNC\s*\(\s*SYSDATE\s*\)\s*-\s*(\d+)",
        _repl_trunc_sysdate_minus,
        result, flags=re.IGNORECASE
    )

    # trunc(sysdate) standalone
    result = re.sub(
        r"\bTRUNC\s*\(\s*SYSDATE\s*\)",
        "CAST(GETDATE() AS DATE)",
        result, flags=re.IGNORECASE
    )

    return result, warnings


# ── Pipe concatenation || → + ──────────────────────────────────────────────


def _transform_pipe_concat(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    Replace PostgreSQL || string concatenation operator with T-SQL + operator.

    Must handle:
      expr1 || expr2              → expr1 + expr2
      expr1 || ' - ' || expr2     → expr1 + ' - ' + expr2

    Skips || inside string literals.
    Uses a simple character scan to avoid replacing || inside quotes.
    """
    warnings: list[ConversionWarning] = []
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
            result.append(' + ')
            i += 2
            # Skip surrounding whitespace collapse (keep one space)
            while i < len(s) and s[i] == ' ':
                i += 1
            continue
        else:
            result.append(ch)
        i += 1

    return ''.join(result), warnings


# ── INITCAP → UPPER with inline comment ──────────────────────────────────────


def _transform_initcap(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    INITCAP(expr) → UPPER(expr) /* ⚠ INITCAP not supported in Fabric — replaced with UPPER */

    INITCAP (title-case) has no native equivalent in Fabric Warehouse T-SQL.
    UPPER is the closest safe substitute; annotate for human review.
    """
    warnings: list[ConversionWarning] = []
    result = sql

    if not re.search(r"\bINITCAP\s*\(", result, re.IGNORECASE):
        return result, warnings

    result = re.sub(
        r"\bINITCAP\s*\(",
        "UPPER( /* ⚠ INITCAP not supported in Fabric — replaced with UPPER */",
        result, flags=re.IGNORECASE
    )

    warnings.append(ConversionWarning(
        level=WarningLevel.WARNING,
        code="INITCAP_REPLACED",
        message=(
            "INITCAP() is not supported in Fabric Warehouse. "
            "Replaced with UPPER(). Result will be ALL CAPS, not Title Case."
        ),
        suggestion=(
            "If Title Case is required, implement a user-defined scalar function "
            "or handle capitalisation in the application/reporting layer."
        ),
    ))

    return result, warnings




# ── Ordinal GROUP BY expansion ────────────────────────────────────────────────


def _split_select_columns(s: str) -> list[str]:
    """
    Split a SELECT column list on depth-0 commas.
    CASE/END-aware: commas inside CASE...END are NOT column separators.
    Also handles nested parens, string literals.
    """
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
    """
    Remove trailing ' AS alias' at depth-0 from a SELECT column expression.
    Preserves AS inside CAST(x AS type) and CASE...END blocks.
    """
    s = col_expr.strip()
    paren_depth, case_depth, i, last_as_pos = 0, 0, 0, -1
    while i < len(s):
        ch = s[i]
        if ch in ("'", '"'):
            q = ch; i += 1
            while i < len(s) and s[i] != q: i += 1
        elif ch == '(': paren_depth += 1
        elif ch == ')': paren_depth -= 1
        elif paren_depth == 0 and re.match(r'CASE[\s\n(]', s[i:i+5], re.IGNORECASE):
            case_depth += 1
        elif paren_depth == 0 and case_depth > 0 and re.match(r'END[\s\n,);]', s[i:i+4], re.IGNORECASE):
            case_depth -= 1
        elif paren_depth == 0 and case_depth == 0 and s[i:i+4].upper() == ' AS ':
            last_as_pos = i
        i += 1
    return s[:last_as_pos].strip() if last_as_pos > 0 else s.strip()


def _is_aggregate_expr(expr: str) -> bool:
    """Return True if expression is a top-level aggregate function call."""
    return bool(re.match(
        r'^\s*(SUM|COUNT|MAX|MIN|AVG|STDEV|STDDEV|VARIANCE|VAR|LISTAGG|STRING_AGG' 
        r'|APPROX_COUNT_DISTINCT|PERCENTILE_CONT|PERCENTILE_DISC)\s*\(',
        expr.strip(), re.IGNORECASE))


def _find_select_cols_for_groupby(sql: str, gb_start: int) -> list[str] | None:
    """
    Walk backwards from gb_start to find the enclosing SELECT at the same
    paren nesting level. Extract its column list (SELECT ... FROM).
    Returns list of column expression strings, or None if not found.
    """
    # Walk back to find SELECT at depth 0
    depth = 0
    j = gb_start - 1
    select_end = -1   # position right after 'SELECT'
    while j >= 0:
        ch = sql[j]
        if ch == ')':   depth += 1
        elif ch == '(':
            if depth > 0: depth -= 1
            else: return None   # inside subquery — stop
        elif depth == 0:
            # Check for SELECT keyword (going backwards, check from start of word)
            if sql[j:j+6].upper() == 'SELECT':
                select_end = j + 6
                break
        j -= 1

    if select_end < 0:
        return None

    # Body from after SELECT to the GROUP BY
    body = sql[select_end:gb_start]

    # Find FROM at depth-0 and case-depth-0
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


def _transform_ordinal_groupby(sql: str) -> tuple[str, list[ConversionWarning]]:
    """
    Expand ordinal GROUP BY (GROUP BY 1, 2, 3, ...) into explicit column
    expressions — required because Fabric T-SQL does not support ordinal
    GROUP BY positions.

    Algorithm per GROUP BY clause:
      1. Locate the SELECT that owns this GROUP BY (same paren depth).
      2. Parse its column list with CASE/END-aware splitter.
      3. For each ordinal N: get the Nth column, strip its alias.
      4. Skip aggregate functions (SUM/MAX/COUNT etc.) — cannot GROUP BY an agg.
      5. Collapse multi-line whitespace in CASE expressions to single line.
      6. Replace GROUP BY 1,2,... with GROUP BY\n    col1,\n    col2,...

    Reference: Doc 8 (expected output) GROUP BY style.
    """
    warnings_out: list[ConversionWarning] = []

    # Match GROUP BY followed by a list that is ALL integers (ordinal)
    # Stop before ) ; or end-of-string
    gb_re = re.compile(
        r'\bGROUP\s+BY\s+((?:\d+\s*,\s*)*\d+)\s*(?=[)\n;]|$)',
        re.IGNORECASE
    )

    result = sql
    matches = list(gb_re.finditer(result))
    if not matches:
        return result, warnings_out

    # Process in reverse order to preserve character positions
    for m in reversed(matches):
        ordinals = [int(x.strip()) for x in m.group(1).split(',') if x.strip().isdigit()]
        if not ordinals:
            continue

        gb_start = m.start()
        all_cols = _find_select_cols_for_groupby(result, gb_start)
        if all_cols is None:
            warnings_out.append(ConversionWarning(
                level=WarningLevel.WARNING,
                code="GROUPBY_EXPAND_FAILED",
                message=f"Could not expand ordinal GROUP BY at character {gb_start} — SELECT list not found.",
                suggestion="Manually replace GROUP BY 1,2,... with explicit column names.",
            ))
            continue

        expanded = []
        for n in ordinals:
            if n < 1 or n > len(all_cols):
                expanded.append(f"/* ordinal {n} out of range — verify manually */")
                continue
            raw_expr = all_cols[n - 1]
            bare = _strip_col_alias(raw_expr)
            # Normalise multi-line whitespace to single spaces
            bare = re.sub(r'\s+', ' ', bare).strip()
            if _is_aggregate_expr(bare):
                continue   # aggregates cannot be in GROUP BY
            expanded.append(bare)

        if not expanded:
            continue

        # Detect current line indentation for alignment
        line_start = result.rfind('\n', 0, gb_start) + 1
        raw_line   = result[line_start:gb_start]
        indent     = len(raw_line) - len(raw_line.lstrip())
        col_indent = ' ' * indent + '    '

        formatted = (',\n' + col_indent).join(expanded)
        new_gb    = f"GROUP BY\n{col_indent}{formatted}"

        result = result[:m.start()] + new_gb + result[m.end():]

    if matches:
        warnings_out.append(ConversionWarning(
            level=WarningLevel.WARNING,
            code="ORDINAL_GROUPBY_EXPANDED",
            message=(
                f"Ordinal GROUP BY positions expanded to explicit column names "
                f"({len(matches)} GROUP BY clause(s) processed). "
                "Fabric T-SQL does not support positional GROUP BY."
            ),
            suggestion=(
                "Review expanded GROUP BY columns — CASE expressions are "
                "included verbatim (normalised to single line). "
                "Aggregate columns (SUM/MAX/COUNT) are automatically excluded."
            ),
        ))

    return result, warnings_out


# ── View object header ────────────────────────────────────────────────────────


def _view_header(
    obj_type_label: str,
    source_name: str,
    target_name: str,
    status,
    confidence: float,
    warnings: list,
) -> str:
    """
    Rich comment block prepended to every VIEW / PROCEDURE output.

    Example:
        -- ══════════════════════════════════════════════════════════════════
        -- VIEW    : bi_alefdw.v_student_login_summary
        -- Target  : ${os_bi_alefdw}.v_student_login_summary
        -- Status  : ⚠️  PARTIAL  |  Confidence: 90%
        -- Warnings: 1  <- see ⚠ markers in the report
        --   ⚠ MD5_FUNCTION : md5() is not native in Fabric Warehouse.
        --   💡 Replace with HASHBYTES or create a user-defined md5 function.
        -- ══════════════════════════════════════════════════════════════════
    """
    from app.core.models import ConversionStatus, WarningLevel

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
        f"-- {obj_type_label:<8}: {source_name}",
        f"-- Target  : {target_name}",
        f"-- Status  : {status_icon} {status.value}  |  Confidence: {confidence:.0%}",
        f"-- Warnings: {warn_count}",
    ]

    # List each warning fully — NO truncation, wrap at 100 chars
    from textwrap import wrap as _wrap
    WRAP_WIDTH = 100
    PREFIX_WARN = "--   "          # 5 chars
    PREFIX_CONT = "--      "       # 7 chars — indent for continuation lines
    PREFIX_SUGG = "--   💡 "

    for w in warnings:
        icon = "❌" if w.level == WarningLevel.ERROR else "⚠"
        first_line = f"{icon} {w.code}: {w.message}"
        wrapped = _wrap(first_line, width=WRAP_WIDTH,
                        initial_indent=PREFIX_WARN,
                        subsequent_indent=PREFIX_CONT)
        lines.extend(wrapped)

        if w.suggestion and w.suggestion.strip() and w.suggestion != w.message:
            sugg_wrapped = _wrap(w.suggestion, width=WRAP_WIDTH,
                                 initial_indent=PREFIX_SUGG,
                                 subsequent_indent=PREFIX_CONT)
            lines.extend(sugg_wrapped)

    lines.append(f"-- {border}")
    lines.append("")   # blank line before DDL
    return "\n".join(lines) + "\n"
