"""
Table DDL Parser

Converts a Redshift CREATE TABLE statement into a TableIR object.

Strategy:
  - Use regex-based token extraction for the outer structure
    (table name, schema, column list, table-level clauses).
  - sqlglot is used as a secondary layer to parse individual column
    type strings reliably.
  - Redshift-specific clauses (ENCODE, DISTKEY, DISTSTYLE, SORTKEY, BACKUP)
    are extracted into IR metadata, then stripped from the output.

Why not pure sqlglot?
  sqlglot's Redshift dialect handles most DDL but occasionally chokes on
  multi-word ENCODE clause values (AZ64, LZO, BYTEDICT, RAW, ZSTD …) and
  combined DISTKEY qualifiers on a column definition.  The hybrid approach
  gives us the best of both worlds.
"""
from __future__ import annotations

import re
from typing import Optional

import sqlglot
import sqlglot.expressions as exp

from app.core.models import (
    ColumnIR,
    ConversionStatus,
    ConversionWarning,
    TableIR,
    WarningLevel,
)
from app.core.rules import DATATYPE_MAP, TSQL_RESERVED_WORDS, VARCHAR_MAX_THRESHOLD
from app.logging.logger import get_logger

log = get_logger("table_parser")

# ── Redshift clause patterns ──────────────────────────────────────────────────

# Strips table-level DISTSTYLE, DISTKEY, SORTKEY, BACKUP, ENCODE DEFAULT
_TABLE_CLAUSE_RE = re.compile(
    r"""
    (?:
        DISTSTYLE\s+\w+                          |   # DISTSTYLE AUTO/KEY/ALL/EVEN
        DISTKEY\s*\([^)]+\)                      |   # DISTKEY(col)
        (?:COMPOUND\s+|INTERLEAVED\s+)?SORTKEY   \s*\([^)]+\)  |  # SORTKEY(cols)
        BACKUP\s+(?:YES|NO)                      |   # BACKUP NO/YES
        ENCODE\s+\w+                             |   # ENCODE DEFAULT
        ENCODE\s+DEFAULT                             # ENCODE DEFAULT
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Column-level ENCODE clause (e.g. ENCODE az64)
_COL_ENCODE_RE = re.compile(r"\bENCODE\s+\S+", re.IGNORECASE)

# Column-level DISTKEY qualifier
_COL_DISTKEY_RE = re.compile(r"\bDISTKEY\b", re.IGNORECASE)

# SORTKEY extraction
_SORTKEY_RE = re.compile(
    r"(?:(COMPOUND|INTERLEAVED)\s+)?SORTKEY\s*\(([^)]+)\)",
    re.IGNORECASE,
)

# DISTSTYLE
_DISTSTYLE_RE = re.compile(r"DISTSTYLE\s+(\w+)", re.IGNORECASE)

# DISTKEY (table-level)
_DISTKEY_TABLE_RE = re.compile(r"DISTKEY\s*\(([^)]+)\)", re.IGNORECASE)

# Schema.TableName from CREATE TABLE
_TABLE_NAME_RE = re.compile(
    r"CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+([^\s(]+)",
    re.IGNORECASE,
)

# Body between outermost parentheses (column definitions)
# Allow optional trailing whitespace and semicolon after the closing paren
_BODY_RE = re.compile(r"\((.+)\)\s*;?\s*$", re.DOTALL)


# ── Public API ────────────────────────────────────────────────────────────────


def parse_table(sql: str) -> TableIR:
    """
    Parse a Redshift CREATE TABLE statement and return a TableIR.

    Raises:
        ValueError: if the statement cannot be recognised as a CREATE TABLE.
    """
    # ── 1. Extract schema.table name ─────────────────────────────────────
    name_match = _TABLE_NAME_RE.search(sql)
    if not name_match:
        raise ValueError(f"Cannot extract table name from: {sql[:120]!r}")

    full_name = name_match.group(1).strip().strip('"')
    if "." in full_name:
        schema, table_name = full_name.rsplit(".", 1)
    else:
        schema, table_name = "", full_name

    schema = schema.strip('"')
    table_name = table_name.strip('"')

    warnings: list[ConversionWarning] = []

    # ── 2. Extract table-level metadata ──────────────────────────────────
    diststyle_m = _DISTSTYLE_RE.search(sql)
    diststyle = diststyle_m.group(1).upper() if diststyle_m else None

    distkey_m = _DISTKEY_TABLE_RE.search(sql)
    distkey = distkey_m.group(1).strip() if distkey_m else None

    sortkey_m = _SORTKEY_RE.search(sql)
    sortkey_type: Optional[str] = None
    sortkeys: list[str] = []
    if sortkey_m:
        sortkey_type = sortkey_m.group(1).upper() if sortkey_m.group(1) else "COMPOUND"
        sortkeys = [k.strip() for k in sortkey_m.group(2).split(",")]

    # Warn on INTERLEAVED sortkey
    if sortkey_type == "INTERLEAVED":
        warnings.append(ConversionWarning(
            level=WarningLevel.WARNING,
            code="INTERLEAVED_SORTKEY",
            message="INTERLEAVED SORTKEY is Redshift-specific and has no Fabric equivalent.",
            suggestion="Sort order management in Fabric is handled automatically. Clause removed.",
        ))

    # ── 3. Strip table-level clauses from SQL before column parsing ───────
    # We need only the column block; strip everything after the closing )
    # of the column list plus table-level clauses.
    stripped_sql = _TABLE_CLAUSE_RE.sub("", sql)

    # ── 4. Extract column definitions ────────────────────────────────────
    body_match = _BODY_RE.search(stripped_sql)
    if not body_match:
        raise ValueError(f"Cannot extract column body from: {sql[:120]!r}")

    col_block = body_match.group(1)
    columns = _parse_columns(col_block, warnings)

    ir = TableIR(
        schema=schema,
        name=table_name,
        columns=columns,
        diststyle=diststyle,
        distkey=distkey,
        sortkeys=sortkeys,
        sortkey_type=sortkey_type,
        warnings=warnings,
    )

    # ── 5. Determine overall status ───────────────────────────────────────
    has_manual = any(c.warnings for c in columns for w in c.warnings
                     if w.level == WarningLevel.ERROR)
    has_warn = bool(warnings) or any(c.warnings for c in columns)

    if has_manual:
        ir.status = ConversionStatus.MANUAL_REVIEW
        ir.confidence_score = 0.5
    elif has_warn:
        ir.status = ConversionStatus.PARTIAL
        ir.confidence_score = 0.80
    else:
        ir.status = ConversionStatus.HIGH_CONFIDENCE
        ir.confidence_score = 1.0

    return ir


# ── Column parsing ────────────────────────────────────────────────────────────


def _parse_columns(col_block: str, table_warnings: list[ConversionWarning]) -> list[ColumnIR]:
    """
    Parse the column definition block of a CREATE TABLE statement.

    Splits on commas that are NOT inside parentheses, then parses each
    column definition individually.
    """
    col_defs = _split_column_defs(col_block)
    columns: list[ColumnIR] = []
    for raw in col_defs:
        raw = raw.strip()
        if not raw:
            continue
        # Skip table-level constraints (PRIMARY KEY, FOREIGN KEY, UNIQUE, CHECK)
        if re.match(r"(PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK)\b", raw, re.IGNORECASE):
            continue
        col = _parse_single_column(raw)
        if col:
            columns.append(col)
    return columns


def _split_column_defs(col_block: str) -> list[str]:
    """Split column block on commas not inside parentheses."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []

    for ch in col_block:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)

    if current:
        parts.append("".join(current))

    return parts


def _parse_single_column(raw: str) -> Optional[ColumnIR]:
    """Parse a single column definition string into a ColumnIR."""
    raw = raw.strip()
    if not raw:
        return None

    warnings: list[ConversionWarning] = []

    # ── Extract and remove ENCODE clause ─────────────────────────────────
    encode_match = _COL_ENCODE_RE.search(raw)
    encode_val: Optional[str] = None
    if encode_match:
        encode_val = encode_match.group(0).split()[-1].upper()
        raw = _COL_ENCODE_RE.sub("", raw).strip()

    # ── Extract and remove DISTKEY qualifier ─────────────────────────────
    is_distkey = bool(_COL_DISTKEY_RE.search(raw))
    if is_distkey:
        raw = _COL_DISTKEY_RE.sub("", raw).strip()

    # ── Split into name + type ────────────────────────────────────────────
    # Column names with spaces are quoted or unquoted — handle both
    # Pattern: name may contain spaces if the full original had no quotes
    # We use a conservative split: first token(s) that look like a type keyword

    col_name, type_str = _split_name_type(raw)
    if not col_name or not type_str:
        log.warning("cannot_parse_column", raw=raw[:80])
        return None

    # ── NOT NULL / DEFAULT ────────────────────────────────────────────────
    is_nullable = True
    default_value: Optional[str] = None
    is_identity = False

    not_null_m = re.search(r"\bNOT\s+NULL\b", type_str, re.IGNORECASE)
    if not_null_m:
        is_nullable = False
        type_str = type_str[: not_null_m.start()].strip()

    default_m = re.search(r"\bDEFAULT\s+(.+?)(?:\s+NOT\s+NULL\s*)?$", type_str, re.IGNORECASE)
    if default_m:
        default_value = default_m.group(1).strip()
        type_str = type_str[: default_m.start()].strip()

    identity_m = re.search(r"\bIDENTITY\s*\([^)]*\)", type_str, re.IGNORECASE)
    if identity_m:
        is_identity = True
        warnings.append(ConversionWarning(
            level=WarningLevel.WARNING,
            code="IDENTITY_COLUMN",
            message=f"Column '{col_name}' uses IDENTITY — verify Fabric IDENTITY syntax.",
            suggestion="Fabric supports IDENTITY(seed, increment) on INT/BIGINT columns.",
            original_fragment=identity_m.group(0),
        ))
        type_str = type_str[: identity_m.start()].strip()

    # ── Map datatype ──────────────────────────────────────────────────────
    fabric_type, type_warnings = _map_datatype(col_name, type_str.strip())
    warnings.extend(type_warnings)

    # ── Handle reserved word / spaces in name ────────────────────────────
    is_reserved = col_name.upper() in TSQL_RESERVED_WORDS
    has_spaces = " " in col_name

    return ColumnIR(
        name=col_name,
        original_type=type_str,
        fabric_type=fabric_type,
        is_nullable=is_nullable,
        default_value=default_value,
        is_identity=is_identity,
        encode=encode_val,
        is_distkey=is_distkey,
        is_reserved_word=is_reserved,
        contains_spaces=has_spaces,
        warnings=warnings,
    )


# Regex to identify the boundary between column name and type
# Redshift/Postgres type keywords that begin a type definition
_TYPE_KEYWORDS = re.compile(
    r"""
    (?ix)
    \b(
        bigint | int8 | integer | int4 | int2 | int | smallint |
        double\s+precision | float8 | float4 | float | real |
        numeric | decimal |
        character\s+varying | character | varchar | nvarchar | bpchar | char |
        text |
        boolean | bool |
        timestamp\s+without\s+time\s+zone | timestamp\s+with\s+time\s+zone |
        timestamp | timezoneoid | date | time | timetz |
        super | geometry | geography | varbyte | hllsketch
    )\b
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _split_name_type(raw: str) -> tuple[str, str]:
    """
    Split 'col_name TYPE rest' into (name, 'TYPE rest').

    Handles:
      - Simple: "school_dw_id bigint"
      - With spaces in name: "school name character varying(256)"
      - Quoted: '"school name" character varying(256)'
    """
    raw = raw.strip()

    # Quoted identifier
    if raw.startswith('"'):
        end_quote = raw.index('"', 1)
        col_name = raw[1:end_quote]
        type_part = raw[end_quote + 1:].strip()
        return col_name, type_part

    # Unquoted — find where the type keyword begins
    m = _TYPE_KEYWORDS.search(raw)
    if m:
        col_name = raw[: m.start()].strip()
        type_part = raw[m.start():].strip()
        return col_name, type_part

    # Fallback: first whitespace split
    parts = raw.split(None, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return raw, ""


# ── Datatype mapping ──────────────────────────────────────────────────────────


def _map_datatype(col_name: str, type_str: str) -> tuple[str, list[ConversionWarning]]:
    """
    Convert a Redshift type string to its Fabric equivalent.

    Returns (fabric_type, warnings).
    """
    warnings: list[ConversionWarning] = []
    upper = type_str.upper().strip()

    # Numeric with precision: numeric(18,0), decimal(10,6)
    num_m = re.match(r"(NUMERIC|DECIMAL)\s*\((\d+)\s*,\s*(\d+)\)", upper)
    if num_m:
        return f"DECIMAL({num_m.group(2)},{num_m.group(3)})", warnings

    # Numeric without precision
    if re.match(r"(NUMERIC|DECIMAL)\s*$", upper):
        return "DECIMAL(18,0)", warnings

    # VARCHAR with length
    vc_m = re.match(r"(?:CHARACTER\s+VARYING|VARCHAR|NVARCHAR|CHARACTER|CHAR|BPCHAR)\s*\((\d+)\)", upper)
    if vc_m:
        length = int(vc_m.group(1))
        base = "NVARCHAR" if "NVARCHAR" in upper else "VARCHAR"
        if length > VARCHAR_MAX_THRESHOLD or length == 65535:
            return f"{base}(MAX)", warnings
        return f"{base}({length})", warnings

    # VARCHAR without length
    if re.match(r"(?:CHARACTER\s+VARYING|VARCHAR)\s*$", upper):
        return "VARCHAR(MAX)", warnings

    # CHAR without length
    if re.match(r"(?:CHARACTER|CHAR|BPCHAR)\s*$", upper):
        return "CHAR(1)", warnings

    # Timestamp variants
    if "TIMESTAMP WITHOUT TIME ZONE" in upper or upper == "TIMESTAMP":
        return "DATETIME2(6)", warnings
    if "TIMESTAMP WITH TIME ZONE" in upper:
        w = ConversionWarning(
            level=WarningLevel.WARNING,
            code="TIMEZONE_STRIPPED",
            message=f"Column '{col_name}': timestamp with time zone → DATETIME2(6). Timezone info not preserved.",
            suggestion="Ensure all timestamp values are stored in UTC before migration.",
        )
        warnings.append(w)
        return "DATETIME2(6)", warnings

    # Double precision
    if upper in ("DOUBLE PRECISION", "FLOAT8", "FLOAT"):
        return "FLOAT(53)", warnings
    if upper in ("FLOAT4", "REAL"):
        return "REAL", warnings

    # Boolean
    if upper in ("BOOLEAN", "BOOL"):
        return "BIT", warnings

    # Look up in DATATYPE_MAP by base keyword
    for key, mapping in DATATYPE_MAP.items():
        if upper == key or upper.startswith(key + " ") or upper.startswith(key + "("):
            fabric_type = mapping["fabric_type"]
            if mapping.get("severity") in ("WARN", "MANUAL"):
                warnings.append(ConversionWarning(
                    level=WarningLevel.WARNING,
                    code=f"DATATYPE_{key.replace(' ', '_')}",
                    message=mapping.get("note", f"Type {key} requires review."),
                    suggestion=mapping.get("note", ""),
                    original_fragment=type_str,
                ))
            return fabric_type, warnings

    # Unknown type — pass through with warning
    warnings.append(ConversionWarning(
        level=WarningLevel.WARNING,
        code="UNKNOWN_DATATYPE",
        message=f"Column '{col_name}': unrecognised type '{type_str}' — passed through unchanged.",
        suggestion="Verify this type is supported in Fabric Warehouse.",
        original_fragment=type_str,
    ))
    return type_str.upper(), warnings
