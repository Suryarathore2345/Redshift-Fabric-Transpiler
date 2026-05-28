"""
Rule Registry — single source of truth for every transformation mapping.

Organised into:
  • DATATYPE_MAP      — Redshift type → Fabric T-SQL type
  • FUNCTION_MAP      — Redshift function → Fabric equivalent or warning
  • SYNTAX_RULES      — pattern-level structural rewrite rules
  • UNSUPPORTED       — features with no Fabric equivalent
  • RESERVED_WORDS    — T-SQL reserved words that need [bracket] quoting
  • SUFFIX_STRIP      — name suffixes stripped on Redshift objects (_mv, _view)

All entries are plain Python dicts/lists so they can later be externalised
to YAML without touching transformation code.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


# ── Rule severity ─────────────────────────────────────────────────────────────


class RuleSeverity(str, Enum):
    AUTO = "AUTO"           # fully handled automatically
    WARN = "WARN"           # converted with a warning
    MANUAL = "MANUAL"       # cannot convert; flag for human review
    UNSUPPORTED = "UNSUPPORTED"  # no equivalent in Fabric


# ── Datatype map ─────────────────────────────────────────────────────────────
#
# Keys are uppercased Redshift base type names (without precision).
# Values are either:
#   str  → direct replacement
#   dict → {"type": str, "severity": RuleSeverity, "note": str}
#
# Derivation from reference DDLs (bi_alefdw_tables.sql → V3/V7 migration files):
#
#   bigint                   → BIGINT          (direct)
#   integer / int4           → INT             (direct)
#   smallint / int2          → SMALLINT        (direct)
#   double precision / float8→ FLOAT(53)       (matches reference exactly)
#   numeric(p,s)             → DECIMAL(p,s)    (reference: numeric(10,6)→DECIMAL(10,6))
#   character varying(n)     → VARCHAR(n)      (direct)
#   character varying(65535) → VARCHAR(MAX)    (reference: school_label pattern)
#   character varying(8000+) → VARCHAR(MAX)    (8000+ → MAX in Fabric)
#   boolean                  → BIT             (reference: outside_school_flag BIT)
#   timestamp without tz     → DATETIME2(6)    (reference: login_date_time DATETIME2(6))
#   timestamp with tz        → DATETIME2(6)    (reference: inserted_at DATETIME2(6))
#   date                     → DATE            (direct)
#   text                     → VARCHAR(MAX)    (no TEXT in Fabric warehouse)
#   geometry                 → VARCHAR(MAX)+WARN (no spatial in Fabric warehouse)
#   super                    → VARCHAR(MAX)+WARN (Redshift-only semi-structured type)
#   varbyte                  → VARBINARY(MAX)+WARN
#   hllsketch                → MANUAL          (no equivalent)

VARCHAR_MAX_THRESHOLD = 8000   # VARCHAR beyond this → VARCHAR(MAX)

DATATYPE_MAP: dict[str, dict] = {
    # ── Integers ──────────────────────────────────────────────────────────
    "BIGINT":            {"fabric_type": "BIGINT",       "severity": RuleSeverity.AUTO},
    "INT8":              {"fabric_type": "BIGINT",       "severity": RuleSeverity.AUTO},
    "INTEGER":           {"fabric_type": "INT",          "severity": RuleSeverity.AUTO},
    "INT":               {"fabric_type": "INT",          "severity": RuleSeverity.AUTO},
    "INT4":              {"fabric_type": "INT",          "severity": RuleSeverity.AUTO},
    "SMALLINT":          {"fabric_type": "SMALLINT",     "severity": RuleSeverity.AUTO},
    "INT2":              {"fabric_type": "SMALLINT",     "severity": RuleSeverity.AUTO},

    # ── Floats ────────────────────────────────────────────────────────────
    "DOUBLE PRECISION":  {"fabric_type": "FLOAT(53)",    "severity": RuleSeverity.AUTO},
    "FLOAT8":            {"fabric_type": "FLOAT(53)",    "severity": RuleSeverity.AUTO},
    "FLOAT":             {"fabric_type": "FLOAT(53)",    "severity": RuleSeverity.AUTO},
    "FLOAT4":            {"fabric_type": "REAL",         "severity": RuleSeverity.AUTO},
    "REAL":              {"fabric_type": "REAL",         "severity": RuleSeverity.AUTO},

    # ── Fixed precision ───────────────────────────────────────────────────
    "NUMERIC":           {"fabric_type": "DECIMAL",      "severity": RuleSeverity.AUTO},
    "DECIMAL":           {"fabric_type": "DECIMAL",      "severity": RuleSeverity.AUTO},

    # ── Strings ───────────────────────────────────────────────────────────
    "CHARACTER VARYING": {"fabric_type": "VARCHAR",      "severity": RuleSeverity.AUTO},
    "VARCHAR":           {"fabric_type": "VARCHAR",      "severity": RuleSeverity.AUTO},
    "NVARCHAR":          {"fabric_type": "NVARCHAR",     "severity": RuleSeverity.AUTO},
    "CHAR":              {"fabric_type": "CHAR",         "severity": RuleSeverity.AUTO},
    "CHARACTER":         {"fabric_type": "CHAR",         "severity": RuleSeverity.AUTO},
    "BPCHAR":            {"fabric_type": "CHAR",         "severity": RuleSeverity.AUTO},
    "TEXT":              {
        "fabric_type": "VARCHAR(MAX)",
        "severity": RuleSeverity.WARN,
        "note": "Redshift TEXT mapped to VARCHAR(MAX); verify max length requirements.",
    },

    # ── Boolean ───────────────────────────────────────────────────────────
    "BOOLEAN":           {"fabric_type": "BIT",          "severity": RuleSeverity.AUTO},
    "BOOL":              {"fabric_type": "BIT",          "severity": RuleSeverity.AUTO},

    # ── Date / Time ───────────────────────────────────────────────────────
    "DATE":              {"fabric_type": "DATE",         "severity": RuleSeverity.AUTO},
    "TIMESTAMP":         {"fabric_type": "DATETIME2(6)", "severity": RuleSeverity.AUTO},
    "TIMESTAMP WITHOUT TIME ZONE": {
        "fabric_type": "DATETIME2(6)", "severity": RuleSeverity.AUTO,
    },
    "TIMESTAMP WITH TIME ZONE": {
        "fabric_type": "DATETIME2(6)",
        "severity": RuleSeverity.WARN,
        "note": "Timezone info stripped; Fabric DATETIME2(6) is always UTC-naive.",
    },
    "TIMEZONEOID":       {"fabric_type": "DATETIME2(6)", "severity": RuleSeverity.WARN},
    "TIME":              {"fabric_type": "TIME",         "severity": RuleSeverity.AUTO},
    "TIMETZ":            {"fabric_type": "TIME",         "severity": RuleSeverity.WARN},

    # ── Redshift-specific (unsupported / downgraded) ───────────────────────
    "SUPER": {
        "fabric_type": "VARCHAR(MAX)",
        "severity": RuleSeverity.WARN,
        "note": (
            "Redshift SUPER (semi-structured) has no direct Fabric equivalent. "
            "Mapped to VARCHAR(MAX). Consider using JSON functions in application layer."
        ),
    },
    "GEOMETRY": {
        "fabric_type": "VARCHAR(MAX)",
        "severity": RuleSeverity.WARN,
        "note": (
            "Redshift GEOMETRY type is unsupported in Microsoft Fabric Warehouse. "
            "Mapped to VARCHAR(MAX) as WKT representation. Spatial queries will not work."
        ),
    },
    "GEOGRAPHY": {
        "fabric_type": "VARCHAR(MAX)",
        "severity": RuleSeverity.WARN,
        "note": "Redshift GEOGRAPHY mapped to VARCHAR(MAX). No spatial support in Fabric Warehouse.",
    },
    "VARBYTE": {
        "fabric_type": "VARBINARY(MAX)",
        "severity": RuleSeverity.WARN,
        "note": "Redshift VARBYTE mapped to VARBINARY(MAX). Verify binary handling.",
    },
    "HLLSKETCH": {
        "fabric_type": "VARCHAR(MAX)",
        "severity": RuleSeverity.MANUAL,
        "note": (
            "HyperLogLog sketches are Redshift-proprietary. "
            "No equivalent in Fabric; manual redesign required."
        ),
    },
}


# ── Function conversion map ───────────────────────────────────────────────────
#
# Structure:
#   key   → uppercased Redshift function name
#   value → {
#       "fabric_fn": str | None,    # None = flag as unsupported
#       "severity": RuleSeverity,
#       "note": str,
#       "pattern": str | None,      # regex pattern for more complex rewrites
#       "replacement": str | None,  # replacement pattern (uses groups)
#   }
#
# Sources: reference repo diff + Fabric T-SQL surface area docs

FUNCTION_MAP: dict[str, dict] = {
    # ── Null-handling ─────────────────────────────────────────────────────
    "NVL": {
        "fabric_fn": "ISNULL",
        "severity": RuleSeverity.AUTO,
        "note": "NVL(x,y) → ISNULL(x,y) — direct equivalent in T-SQL.",
    },
    "NVL2": {
        "fabric_fn": "IIF",
        "severity": RuleSeverity.WARN,
        "note": "NVL2(x,a,b) → IIF(x IS NOT NULL, a, b) — requires manual arg reorder.",
    },
    "COALESCE": {
        "fabric_fn": "COALESCE",
        "severity": RuleSeverity.AUTO,
        "note": "COALESCE is ANSI-standard; supported in Fabric.",
    },
    "NULLIF": {
        "fabric_fn": "NULLIF",
        "severity": RuleSeverity.AUTO,
    },

    # ── Date / time ───────────────────────────────────────────────────────
    "GETDATE": {
        "fabric_fn": "GETDATE",
        "severity": RuleSeverity.AUTO,
        "note": "GETDATE() is supported in Fabric Warehouse T-SQL.",
    },
    "SYSDATE": {
        "fabric_fn": "GETDATE()",
        "severity": RuleSeverity.AUTO,
        "note": "SYSDATE → GETDATE().",
    },
    "CURRENT_DATE": {
        "fabric_fn": "CONVERT(DATE, GETDATE())",
        "severity": RuleSeverity.AUTO,
        "note": "CURRENT_DATE → CONVERT(DATE, GETDATE()).",
    },
    "CURRENT_TIMESTAMP": {
        "fabric_fn": "GETDATE()",
        "severity": RuleSeverity.AUTO,
    },
    "DATEADD": {
        "fabric_fn": "DATEADD",
        "severity": RuleSeverity.AUTO,
        "note": "DATEADD is supported in Fabric T-SQL with same signature.",
    },
    "DATEDIFF": {
        "fabric_fn": "DATEDIFF",
        "severity": RuleSeverity.AUTO,
    },
    "DATE_TRUNC": {
        "fabric_fn": "DATETRUNC",
        "severity": RuleSeverity.AUTO,
        "note": (
            "DATE_TRUNC('part', expr) → DATETRUNC(part, expr). "
            "Note: argument order is swapped — datepart comes first in T-SQL. "
            "Week truncation: use iso_week instead of week for ISO-8601 compliance."
        ),
    },
    "DATE_PART": {
        "fabric_fn": "DATEPART",
        "severity": RuleSeverity.AUTO,
        "note": "DATE_PART('part', expr) → DATEPART(part, expr).",
    },
    "EXTRACT": {
        "fabric_fn": "DATEPART",
        "severity": RuleSeverity.AUTO,
        "note": "EXTRACT(part FROM expr) → DATEPART(part, expr).",
    },
    "TO_DATE": {
        "fabric_fn": "CONVERT(DATE, ...)",
        "severity": RuleSeverity.WARN,
        "note": "TO_DATE(str, fmt) → CONVERT(DATE, str). Format specifiers differ; verify.",
    },
    "TO_TIMESTAMP": {
        "fabric_fn": "CONVERT(DATETIME2, ...)",
        "severity": RuleSeverity.WARN,
        "note": "TO_TIMESTAMP has no direct equivalent. Use CONVERT(DATETIME2(6), expr).",
    },
    "ADD_MONTHS": {
        "fabric_fn": "DATEADD(MONTH, n, expr)",
        "severity": RuleSeverity.AUTO,
    },
    "LAST_DAY": {
        "fabric_fn": "EOMONTH",
        "severity": RuleSeverity.AUTO,
        "note": "LAST_DAY(d) → EOMONTH(d).",
    },
    "TRUNC": {
        "fabric_fn": "DATETRUNC / FLOOR",
        "severity": RuleSeverity.WARN,
        "note": (
            "TRUNC() can mean date truncation OR numeric truncation in Redshift. "
            "Date context: DATETRUNC(day, expr). Numeric context: FLOOR(expr)."
        ),
    },

    # ── String ────────────────────────────────────────────────────────────
    "TO_CHAR": {
        "fabric_fn": "FORMAT / CONVERT",
        "severity": RuleSeverity.WARN,
        "note": (
            "TO_CHAR(expr, fmt) has no single T-SQL equivalent. "
            "For dates use FORMAT(expr, fmt). For numbers use FORMAT or CONVERT. "
            "Format strings differ between Redshift/PostgreSQL and T-SQL."
        ),
    },
    "LPAD": {
        "fabric_fn": "RIGHT",
        "severity": RuleSeverity.WARN,
        "note": "LPAD(s,n,c) → RIGHT(REPLICATE(c,n)+s, n). No native LPAD in T-SQL.",
    },
    "RPAD": {
        "fabric_fn": "LEFT",
        "severity": RuleSeverity.WARN,
        "note": "RPAD(s,n,c) → LEFT(s+REPLICATE(c,n), n). No native RPAD in T-SQL.",
    },
    "SPLIT_PART": {
        "fabric_fn": "PARSENAME / STRING_SPLIT",
        "severity": RuleSeverity.WARN,
        "note": (
            "SPLIT_PART(str, delim, pos) has no single equivalent. "
            "For simple cases: PARSENAME(REPLACE(str,delim,'.'), n). "
            "For complex cases: STRING_SPLIT with ORDER BY ordinal."
        ),
    },
    "REGEXP_REPLACE": {
        "fabric_fn": None,
        "severity": RuleSeverity.MANUAL,
        "note": "REGEXP_REPLACE is unsupported in Fabric Warehouse. Requires application-layer rewrite.",
    },
    "REGEXP_SUBSTR": {
        "fabric_fn": None,
        "severity": RuleSeverity.MANUAL,
        "note": "REGEXP_SUBSTR is unsupported in Fabric Warehouse. No native regex extraction.",
    },
    "REGEXP_COUNT": {
        "fabric_fn": None,
        "severity": RuleSeverity.MANUAL,
        "note": "REGEXP_COUNT is unsupported in Fabric Warehouse.",
    },
    "REGEXP_INSTR": {
        "fabric_fn": None,
        "severity": RuleSeverity.MANUAL,
        "note": "REGEXP_INSTR is unsupported in Fabric Warehouse.",
    },
    "LISTAGG": {
        "fabric_fn": "STRING_AGG",
        "severity": RuleSeverity.WARN,
        "note": (
            "LISTAGG(col, delim) WITHIN GROUP (ORDER BY ...) → "
            "STRING_AGG(col, delim) WITHIN GROUP (ORDER BY ...). "
            "DISTINCT handling differs — STRING_AGG does not support DISTINCT."
        ),
    },
    "STRTOL":            {"fabric_fn": None, "severity": RuleSeverity.MANUAL, "note": "No equivalent."},
    "INITCAP": {
        "fabric_fn": None,
        "severity": RuleSeverity.MANUAL,
        "note": "No INITCAP in Fabric Warehouse. Use application-layer or CLR function.",
    },
    "CHARINDEX": {
        "fabric_fn": "CHARINDEX",
        "severity": RuleSeverity.AUTO,
    },
    "POSITION": {
        "fabric_fn": "CHARINDEX",
        "severity": RuleSeverity.AUTO,
        "note": "POSITION(sub IN str) → CHARINDEX(sub, str).",
    },
    "SUBSTRING": {
        "fabric_fn": "SUBSTRING",
        "severity": RuleSeverity.AUTO,
    },
    "SUBSTR": {
        "fabric_fn": "SUBSTRING",
        "severity": RuleSeverity.AUTO,
    },
    "LEN": {
        "fabric_fn": "LEN",
        "severity": RuleSeverity.AUTO,
    },
    "LENGTH": {
        "fabric_fn": "LEN",
        "severity": RuleSeverity.AUTO,
        "note": "LENGTH(str) → LEN(str).",
    },
    "LOWER": {"fabric_fn": "LOWER", "severity": RuleSeverity.AUTO},
    "UPPER": {"fabric_fn": "UPPER", "severity": RuleSeverity.AUTO},
    "TRIM":  {"fabric_fn": "TRIM",  "severity": RuleSeverity.AUTO},
    "LTRIM": {"fabric_fn": "LTRIM", "severity": RuleSeverity.AUTO},
    "RTRIM": {"fabric_fn": "RTRIM", "severity": RuleSeverity.AUTO},
    "REPLACE": {"fabric_fn": "REPLACE", "severity": RuleSeverity.AUTO},
    "REVERSE": {"fabric_fn": "REVERSE", "severity": RuleSeverity.AUTO},
    "CONCAT": {
        "fabric_fn": "CONCAT",
        "severity": RuleSeverity.AUTO,
        "note": "CONCAT is supported in Fabric T-SQL.",
    },

    # ── Math ──────────────────────────────────────────────────────────────
    "ABS":   {"fabric_fn": "ABS",   "severity": RuleSeverity.AUTO},
    "CEIL":  {"fabric_fn": "CEILING", "severity": RuleSeverity.AUTO},
    "CEILING": {"fabric_fn": "CEILING", "severity": RuleSeverity.AUTO},
    "FLOOR": {"fabric_fn": "FLOOR", "severity": RuleSeverity.AUTO},
    "ROUND": {"fabric_fn": "ROUND", "severity": RuleSeverity.AUTO},
    "POWER": {"fabric_fn": "POWER", "severity": RuleSeverity.AUTO},
    "SQRT":  {"fabric_fn": "SQRT",  "severity": RuleSeverity.AUTO},
    "LOG":   {"fabric_fn": "LOG",   "severity": RuleSeverity.AUTO},
    "EXP":   {"fabric_fn": "EXP",   "severity": RuleSeverity.AUTO},
    "MOD":   {"fabric_fn": "%",     "severity": RuleSeverity.AUTO, "note": "MOD(a,b) → a % b."},

    # ── Hashing ───────────────────────────────────────────────────────────
    "MD5": {
        "fabric_fn": "md5",
        "severity": RuleSeverity.WARN,
        "note": (
            "MD5() is not a native Fabric Warehouse function. "
            "Reference repo retains md5() calls — verify that the Fabric environment "
            "has a user-defined md5 scalar function or substitute HASHBYTES('MD5', CAST(x AS VARBINARY(MAX)))."
        ),
    },
    "HASH": {
        "fabric_fn": "HASHBYTES",
        "severity": RuleSeverity.WARN,
        "note": "Redshift HASH → HASHBYTES('MD5', CAST(expr AS VARBINARY(MAX))). Signature differs.",
    },
    "FNV_HASH": {
        "fabric_fn": None,
        "severity": RuleSeverity.MANUAL,
        "note": "FNV_HASH is Redshift-proprietary. No Fabric equivalent.",
    },

    # ── Window / analytic ─────────────────────────────────────────────────
    "QUALIFY": {
        "fabric_fn": None,
        "severity": RuleSeverity.WARN,
        "note": (
            "QUALIFY is a Redshift/Snowflake clause, not valid T-SQL. "
            "Wrap the query in a subquery and add WHERE clause on the window function."
        ),
    },

    # ── Conditional ───────────────────────────────────────────────────────
    "DECODE": {
        "fabric_fn": "CASE WHEN",
        "severity": RuleSeverity.WARN,
        "note": (
            "DECODE(expr, v1, r1, v2, r2, default) → "
            "CASE expr WHEN v1 THEN r1 WHEN v2 THEN r2 ELSE default END. "
            "NULL equality semantics differ; use IS NULL checks explicitly."
        ),
    },
    "IFF": {
        "fabric_fn": "IIF",
        "severity": RuleSeverity.AUTO,
    },
    "GREATEST": {
        "fabric_fn": None,
        "severity": RuleSeverity.WARN,
        "note": "No GREATEST in T-SQL. Use CASE WHEN a >= b THEN a ELSE b END or apply per column.",
    },
    "LEAST": {
        "fabric_fn": None,
        "severity": RuleSeverity.WARN,
        "note": "No LEAST in T-SQL. Use CASE WHEN a <= b THEN a ELSE b END.",
    },

    # ── Type conversion ───────────────────────────────────────────────────
    "CAST":    {"fabric_fn": "CAST",    "severity": RuleSeverity.AUTO},
    "CONVERT": {"fabric_fn": "CONVERT", "severity": RuleSeverity.AUTO},
    "TRY_CAST": {
        "fabric_fn": "TRY_CAST",
        "severity": RuleSeverity.AUTO,
        "note": "TRY_CAST is supported in Fabric T-SQL.",
    },
    "TRY_CONVERT": {
        "fabric_fn": "TRY_CONVERT",
        "severity": RuleSeverity.AUTO,
    },
}


# ── Redshift-specific clauses that must be stripped ───────────────────────────

STRIP_CLAUSES: list[str] = [
    "ENCODE",          # column-level encoding (storage hint)
    "DISTKEY",         # distribution key column qualifier
    "DISTSTYLE",       # distribution style clause
    "SORTKEY",         # sort key clause
    "INTERLEAVED SORTKEY",
    "COMPOUND SORTKEY",
    "BACKUP NO",       # backup setting
    "BACKUP YES",
    "WITH NO SCHEMA BINDING",  # Redshift view clause
    "TEMP",            # temp table keyword (Fabric uses # prefix or session tables)
    "TEMPORARY",
]


# ── Boolean expression rewrites ───────────────────────────────────────────────
#
# Redshift inherits PostgreSQL boolean literals; T-SQL uses BIT / integer.

BOOLEAN_REWRITES: dict[str, str] = {
    "IS TRUE":      "= 1",
    "IS FALSE":     "= 0",
    "IS NOT TRUE":  "<> 1",
    "IS NOT FALSE": "<> 0",
    "= TRUE":       "= 1",
    "= FALSE":      "= 0",
    "!= TRUE":      "<> 1",
    "!= FALSE":     "<> 0",
    "<> TRUE":      "<> 1",
    "<> FALSE":     "<> 0",
}

# Redshift allows unquoted boolean constants in some contexts
BOOLEAN_LITERAL_REWRITES: dict[str, str] = {
    # standalone true/false in WHERE/CASE
    r"\bTRUE\b":  "1",
    r"\bFALSE\b": "0",
}

# ── Syntax-level rewrites ─────────────────────────────────────────────────────

SYNTAX_RULES: list[dict] = [
    # Redshift Postgres cast operator ::
    {
        "id": "CAST_OPERATOR",
        "description": "Replace PostgreSQL cast operator :: with CAST(expr AS type)",
        "severity": RuleSeverity.AUTO,
        "note": "::type → CAST(expr AS type) — handled by cast rewriter.",
    },
    # DATE_TRUNC argument order swap
    {
        "id": "DATE_TRUNC_SWAP",
        "description": "DATE_TRUNC('part', expr) → DATETRUNC(part, expr)",
        "severity": RuleSeverity.AUTO,
    },
    # INTERVAL literals
    {
        "id": "INTERVAL_LITERAL",
        "description": "Rewrite INTERVAL 'n unit' → DATEADD(unit, n, expr)",
        "severity": RuleSeverity.WARN,
        "note": "INTERVAL literals require context-aware rewriting.",
    },
    # Ordinal GROUP BY (GROUP BY 1, 2, 3) → explicit column refs
    {
        "id": "ORDINAL_GROUP_BY",
        "description": "Ordinal GROUP BY positions expanded to explicit column names",
        "severity": RuleSeverity.AUTO,
        "note": "Fabric supports ordinal GROUP BY but explicit names are best practice.",
    },
    # lower() on string in WHERE — T-SQL is case-insensitive by default with most collations
    {
        "id": "LOWER_FILTER",
        "description": "lower(col) comparisons — T-SQL generally case-insensitive; LOWER() retained for safety",
        "severity": RuleSeverity.AUTO,
    },
    # CREATE OR REPLACE VIEW → CREATE OR ALTER VIEW (T-SQL)
    {
        "id": "CREATE_OR_REPLACE_VIEW",
        "description": "CREATE OR REPLACE VIEW → CREATE OR ALTER VIEW",
        "severity": RuleSeverity.AUTO,
    },
    # CREATE MATERIALIZED VIEW → stored procedure pattern
    {
        "id": "MATERIALIZED_VIEW",
        "description": "CREATE MATERIALIZED VIEW → CREATE TABLE + usp_refresh_ stored procedure pattern",
        "severity": RuleSeverity.WARN,
        "note": (
            "Fabric Warehouse does not support materialised views natively. "
            "Pattern: CREATE TABLE <name> + stored procedure usp_refresh_<name> using CTAS."
        ),
    },
    # CURRENT_DATE in expressions
    {
        "id": "CURRENT_DATE",
        "description": "CURRENT_DATE → CONVERT(DATE, GETDATE())",
        "severity": RuleSeverity.AUTO,
    },
    # ::date cast on expressions
    {
        "id": "CAST_DATE",
        "description": "expr::date → CONVERT(DATE, expr)",
        "severity": RuleSeverity.AUTO,
    },
    # ::timestamp cast
    {
        "id": "CAST_TIMESTAMP",
        "description": "expr::timestamp → CONVERT(DATETIME2(6), expr)",
        "severity": RuleSeverity.AUTO,
    },
    # date(expr) function — Redshift date() extracts date part
    {
        "id": "DATE_FUNCTION",
        "description": "date(expr) → CONVERT(DATE, expr)",
        "severity": RuleSeverity.AUTO,
    },
    # IS FALSE / IS TRUE boolean rewrite
    {
        "id": "BOOLEAN_IS_EXPR",
        "description": "IS TRUE/FALSE → = 1/0",
        "severity": RuleSeverity.AUTO,
    },
    # CONCAT with || operator
    {
        "id": "PIPE_CONCAT",
        "description": "|| string concatenation → + operator or CONCAT()",
        "severity": RuleSeverity.AUTO,
    },
]


# ── T-SQL Reserved Words requiring [bracket] quoting ─────────────────────────
#
# Based on reference repo: [key], [geometry], [school name], [school dw id], etc.
# Combined from T-SQL reserved words + column names with spaces.

TSQL_RESERVED_WORDS: set[str] = {
    "KEY", "INDEX", "TABLE", "VIEW", "DATABASE", "SCHEMA", "COLUMN",
    "GEOMETRY", "GEOGRAPHY", "VALUE", "LEVEL", "PERCENT", "FILE",
    "IDENTITY", "PRIMARY", "FOREIGN", "REFERENCES", "UNIQUE", "CHECK",
    "DEFAULT", "CONSTRAINT", "TRIGGER", "PROCEDURE", "FUNCTION",
    "SELECT", "INSERT", "UPDATE", "DELETE", "FROM", "WHERE", "GROUP",
    "ORDER", "HAVING", "JOIN", "INNER", "OUTER", "LEFT", "RIGHT",
    "FULL", "CROSS", "ON", "AS", "AND", "OR", "NOT", "NULL",
    "CASE", "WHEN", "THEN", "ELSE", "END", "CAST", "CONVERT",
    "EXEC", "EXECUTE", "BEGIN", "TRANSACTION", "COMMIT", "ROLLBACK",
    "WITH", "SET", "RETURN", "BREAK", "CONTINUE", "GOTO",
    "IF", "WHILE", "WAITFOR", "PRINT",
    "USER", "ROLE", "GRANT", "REVOKE", "DENY",
    # Redshift-specific that happen to clash
    "ENCODE",
}


# ── Name suffix stripping ─────────────────────────────────────────────────────
#
# When Redshift uses `students_login_mv` (materialised view), the Fabric
# equivalent table is simply `students_login` — _mv and _view are artifacts
# of Redshift's object type naming conventions.

STRIP_SUFFIXES: list[str] = ["_mv", "_view"]


# ── Unsupported features (always flagged for manual review) ───────────────────

UNSUPPORTED_FEATURES: list[dict] = [
    {
        "feature": "IDENTITY column",
        "severity": RuleSeverity.WARN,
        "note": (
            "Redshift IDENTITY(seed,step) → Fabric IDENTITY(seed,step) is supported "
            "in Warehouse tables but syntax differs. Verify."
        ),
    },
    {
        "feature": "INTERLEAVED SORTKEY",
        "severity": RuleSeverity.AUTO,
        "note": "Removed; no equivalent in Fabric.",
    },
    {
        "feature": "DISTSTYLE / DISTKEY",
        "severity": RuleSeverity.AUTO,
        "note": "Removed; Fabric manages distribution automatically.",
    },
    {
        "feature": "BACKUP NO",
        "severity": RuleSeverity.AUTO,
        "note": "Removed; Fabric manages backup automatically.",
    },
    {
        "feature": "TEMP TABLE",
        "severity": RuleSeverity.WARN,
        "note": (
            "Redshift TEMP/TEMPORARY tables → Fabric session-scoped #temp tables or "
            "Warehouse staging tables. Pattern depends on usage context."
        ),
    },
    {
        "feature": "QUALIFY clause",
        "severity": RuleSeverity.WARN,
        "note": "Wrap query in subquery and use WHERE on the window function alias.",
    },
    {
        "feature": "MATERIALIZED VIEW",
        "severity": RuleSeverity.WARN,
        "note": "Convert to CREATE TABLE + stored procedure (usp_refresh_<name>) pattern.",
    },
    {
        "feature": "CREATE OR REPLACE VIEW",
        "severity": RuleSeverity.AUTO,
        "note": "Converted to CREATE OR ALTER VIEW.",
    },
    {
        "feature": "WITH NO SCHEMA BINDING",
        "severity": RuleSeverity.AUTO,
        "note": "Removed; not applicable to Fabric.",
    },
    {
        "feature": "INTERVAL literals",
        "severity": RuleSeverity.WARN,
        "note": "Use DATEADD() instead of INTERVAL 'n unit'.",
    },
    {
        "feature": "PostgreSQL :: cast operator",
        "severity": RuleSeverity.AUTO,
        "note": "Converted to CAST(expr AS type) or CONVERT(type, expr).",
    },
    {
        "feature": "IS TRUE / IS FALSE",
        "severity": RuleSeverity.AUTO,
        "note": "Converted to = 1 / = 0.",
    },
]
