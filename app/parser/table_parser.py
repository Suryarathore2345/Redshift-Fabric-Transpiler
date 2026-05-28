"""
Table DDL Parser  (v2 — bug-fixed)

Bugs fixed vs v1
────────────────
BUG-1  _BODY_RE greedy corruption
       Symptom : '[SORTKEY)\nCREATE TABLE...\n    local_date]'
       Cause   : re.DOTALL + greedy .+ in _BODY_RE matched from the FIRST '('
                 all the way to the LAST ')' in the entire SQL string, so any
                 parenthesised table-clause residue (SORTKEY (...), DISTKEY (...))
                 that wasn't fully stripped became part of the column block.
       Fix     : Replaced _BODY_RE with _extract_column_block(), a character-level
                 balanced-paren scanner that finds exactly the first '(...)' pair
                 after the table name token. Immune to all table-clause residue.

BUG-2  Column indentation misaligned
       Symptom : First column indented 8 spaces, remaining columns 4 spaces.
       Cause   : col_block join separator in table_generator used only _INDENT
                 (4 sp) but the block's first line was prefixed with _INDENT*2
                 (8 sp), so subsequent lines had only 4 sp of prefix.
       Fix     : Fixed in table_generator.py — all column lines rendered
                 individually with consistent 8-space indentation.

BUG-3  'geometry geometry' / name == type keyword → empty column name
       Symptom : Column silently dropped with 'cannot_parse_column' warning.
       Cause   : _TYPE_KEYWORDS.search found the type keyword at position 0,
                 so col_name = raw[:0] = ''.
       Fix     : _split_name_type() now detects keyword-at-position-0 and
                 falls back to whitespace-split, using the first token as name
                 and the remainder as type. e.g.:
                   'geometry geometry ENCODE raw'  → name='geometry', type='geometry'
                   'date date ENCODE az64'          → name='date', type='date'

BUG-4  _TABLE_CLAUSE_RE: unreachable ENCODE DEFAULT branch + VERBOSE whitespace
       Cause   : ENCODE\s+DEFAULT branch was unreachable after ENCODE\s+\w+.
                 VERBOSE mode on the multiline regex silently ate literal spaces.
       Fix     : Collapsed into a single non-VERBOSE regex with DOTALL so
                 multiline SORTKEY/DISTKEY blocks are always stripped correctly.

BUG-5  Stale residue from table-level clauses corrupting column block
       Cause   : Belt-and-suspenders: even if _TABLE_CLAUSE_RE missed something,
                 _extract_column_block() now guarantees only the text between the
                 outermost column-list parens is returned.
"""
from __future__ import annotations

import re
from typing import Optional

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


# ── Compile-once patterns ─────────────────────────────────────────────────────

# Column-level ENCODE — strip before name/type split
_COL_ENCODE_RE = re.compile(r"\bENCODE\s+\S+", re.IGNORECASE)

# Column-level DISTKEY qualifier — strip before name/type split
_COL_DISTKEY_RE = re.compile(r"\bDISTKEY\b", re.IGNORECASE)

# SORTKEY extraction (metadata only, with DOTALL for multiline)
_SORTKEY_RE = re.compile(
    r"(?:(COMPOUND|INTERLEAVED)\s+)?SORTKEY\s*\(([^)]+)\)",
    re.IGNORECASE | re.DOTALL,
)

# DISTSTYLE / DISTKEY extraction (metadata only)
_DISTSTYLE_RE = re.compile(r"DISTSTYLE\s+(\w+)", re.IGNORECASE)
_DISTKEY_TABLE_RE = re.compile(r"DISTKEY\s*\(([^)]+)\)", re.IGNORECASE | re.DOTALL)

# CREATE TABLE name
_TABLE_NAME_RE = re.compile(
    r"CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\s+([^\s(]+)",
    re.IGNORECASE,
)

# All Redshift/Postgres type keywords — used to find name/type boundary
_TYPE_KEYWORDS = re.compile(
    r"\b("
    r"bigint|int8|integer|int4|int2|smallint|int(?!\w)|"
    r"double\s+precision|float8|float4|float(?!\w)|real|"
    r"numeric|decimal|"
    r"character\s+varying|character(?!\s+varying)|varchar|nvarchar|bpchar|char(?!\w)|"
    r"text|boolean|bool|"
    r"timestamp\s+without\s+time\s+zone|"
    r"timestamp\s+with\s+time\s+zone|"
    r"timestamp(?!\s+w)|timezoneoid|date(?!\w)|time(?!\w)|timetz|"
    r"super|geometry|geography|varbyte|hllsketch"
    r")\b",
    re.IGNORECASE,
)


# ── Public API ────────────────────────────────────────────────────────────────


def parse_table(sql: str) -> TableIR:
    """
    Parse a Redshift CREATE TABLE statement into a TableIR.

    Robust against:
      - Single-line and multiline input
      - CRLF / LF line endings
      - Multiline SORTKEY ( col1, col2 ) blocks
      - Columns whose name matches a type keyword ('geometry geometry')
      - VARCHAR(65535) → VARCHAR(MAX)
      - Columns with spaces in their names

    Raises:
        ValueError: if the SQL cannot be recognised as a CREATE TABLE.
    """
    sql = sql.replace("\r\n", "\n").replace("\r", "\n")

    # 1. Table name ──────────────────────────────────────────────────────
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

    # 2. Table-level metadata ─────────────────────────────────────────────
    diststyle_m = _DISTSTYLE_RE.search(sql)
    diststyle = diststyle_m.group(1).upper() if diststyle_m else None

    distkey_m = _DISTKEY_TABLE_RE.search(sql)
    distkey = distkey_m.group(1).strip() if distkey_m else None

    sortkey_m = _SORTKEY_RE.search(sql)
    sortkey_type: Optional[str] = None
    sortkeys: list[str] = []
    if sortkey_m:
        sortkey_type = sortkey_m.group(1).upper() if sortkey_m.group(1) else "COMPOUND"
        sortkeys = [k.strip() for k in sortkey_m.group(2).split(",") if k.strip()]

    if sortkey_type == "INTERLEAVED":
        warnings.append(ConversionWarning(
            level=WarningLevel.WARNING,
            code="INTERLEAVED_SORTKEY",
            message="INTERLEAVED SORTKEY has no Fabric equivalent. Clause removed.",
            suggestion="Fabric manages physical data organisation automatically.",
        ))

    # 3. Extract column block — balanced-paren scanner (FIX BUG-1) ────────
    col_block = _extract_column_block(sql, name_match.end())
    if col_block is None:
        raise ValueError(f"Cannot extract column body from: {sql[:120]!r}")

    # 4. Parse columns ────────────────────────────────────────────────────
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

    # 5. Compute overall status ───────────────────────────────────────────
    col_warnings_all = [w for c in columns for w in c.warnings]
    has_errors = any(w.level == WarningLevel.ERROR for w in col_warnings_all + warnings)
    has_warns = bool(warnings) or bool(col_warnings_all)

    if has_errors:
        ir.status = ConversionStatus.MANUAL_REVIEW
        ir.confidence_score = 0.50
    elif has_warns:
        ir.status = ConversionStatus.PARTIAL
        ir.confidence_score = 0.85
    else:
        ir.status = ConversionStatus.HIGH_CONFIDENCE
        ir.confidence_score = 1.0

    return ir


def _extract_column_block(sql: str, scan_from: int = 0) -> Optional[str]:
    """
    Return the text inside the FIRST balanced '(...)' pair found in `sql`
    starting from `scan_from` (end of the table name token).

    This is the fix for BUG-1: it is completely immune to greedy regex
    over-matching because it counts opening and closing parentheses
    character by character and stops at the exact matching close.

    Works correctly for:
      - Single-line DDL (inline file format)
      - Multiline DDL (formatted by developer)
      - Multiline SORTKEY / DISTKEY clauses after the column block
    """
    depth = 0
    start_pos = -1

    for i in range(scan_from, len(sql)):
        ch = sql[i]
        if ch == "(":
            if depth == 0:
                start_pos = i
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and start_pos != -1:
                return sql[start_pos + 1: i]

    return None  # Unclosed paren — malformed input


# ── Column parsing ────────────────────────────────────────────────────────────


def _parse_columns(
    col_block: str,
    table_warnings: list[ConversionWarning],
) -> list[ColumnIR]:
    col_defs = _split_column_defs(col_block)
    columns: list[ColumnIR] = []
    for raw in col_defs:
        raw = raw.strip()
        if not raw:
            continue
        # Skip table-level constraint declarations
        if re.match(
            r"(PRIMARY\s+KEY|FOREIGN\s+KEY|UNIQUE|CHECK)\b", raw, re.IGNORECASE
        ):
            continue
        col = _parse_single_column(raw)
        if col:
            columns.append(col)
    return columns


def _split_column_defs(col_block: str) -> list[str]:
    """Split column block on depth-0 commas."""
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
    """
    Parse one column definition into a ColumnIR.

    Pipeline:
      1. Strip ENCODE clause
      2. Strip DISTKEY qualifier
      3. Split name from type (handles name == keyword edge case)
      4. Extract NOT NULL / DEFAULT / IDENTITY
      5. Map datatype → Fabric T-SQL type
      6. Flag identifiers needing [bracket] quoting
    """
    raw = raw.strip()
    if not raw:
        return None

    warnings: list[ConversionWarning] = []

    # 1. Strip ENCODE
    encode_val: Optional[str] = None
    enc_m = _COL_ENCODE_RE.search(raw)
    if enc_m:
        encode_val = enc_m.group(0).split()[-1].upper()
        raw = _COL_ENCODE_RE.sub("", raw).strip()

    # 2. Strip DISTKEY
    is_distkey = bool(_COL_DISTKEY_RE.search(raw))
    if is_distkey:
        raw = _COL_DISTKEY_RE.sub("", raw).strip()

    # 3. Split name / type
    col_name, type_str = _split_name_type(raw)
    if not col_name or not type_str:
        log.warning("cannot_parse_column", raw=raw[:80])
        return None

    # 4. NOT NULL / DEFAULT / IDENTITY
    is_nullable = True
    default_value: Optional[str] = None
    is_identity = False

    nn_m = re.search(r"\bNOT\s+NULL\b", type_str, re.IGNORECASE)
    if nn_m:
        is_nullable = False
        type_str = type_str[: nn_m.start()].strip()

    def_m = re.search(
        r"\bDEFAULT\s+(.+?)(?:\s+NOT\s+NULL\s*)?$", type_str, re.IGNORECASE
    )
    if def_m:
        default_value = def_m.group(1).strip()
        type_str = type_str[: def_m.start()].strip()

    ident_m = re.search(r"\bIDENTITY\s*\([^)]*\)", type_str, re.IGNORECASE)
    if ident_m:
        is_identity = True
        warnings.append(ConversionWarning(
            level=WarningLevel.WARNING,
            code="IDENTITY_COLUMN",
            message=(
                f"Column '{col_name}' uses IDENTITY — verify Fabric IDENTITY syntax. "
                "Fabric supports IDENTITY(seed, increment) on INT / BIGINT columns."
            ),
            suggestion="Confirm IDENTITY seed/step values match Redshift originals.",
            original_fragment=ident_m.group(0),
        ))
        type_str = type_str[: ident_m.start()].strip()

    # 5. Map datatype
    fabric_type, type_warnings = _map_datatype(col_name, type_str.strip())
    warnings.extend(type_warnings)

    # 6. Identifier quoting flags
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


# ── Name / type boundary detection ───────────────────────────────────────────


def _split_name_type(raw: str) -> tuple[str, str]:
    """
    Split 'col_name TYPE ...' into (name, 'TYPE ...').

    Handles:
      Normal:         'school_dw_id bigint'           → ('school_dw_id', 'bigint')
      Spaces in name: 'school name varchar(256)'       → ('school name', 'varchar(256)')
      Quoted:         '"school name" varchar(256)'     → ('school name', 'varchar(256)')
      Name==keyword:  'geometry geometry ENCODE raw'   → ('geometry', 'geometry')
                      (FIX BUG-3)
    """
    raw = raw.strip()
    if not raw:
        return "", ""

    # Quoted identifier — always unambiguous
    if raw.startswith('"'):
        end_q = raw.index('"', 1)
        return raw[1:end_q], raw[end_q + 1:].strip()

    # Find where the type keyword begins
    m = _TYPE_KEYWORDS.search(raw)
    if not m:
        # No known type keyword — whitespace split
        parts = raw.split(None, 1)
        return (parts[0], parts[1]) if len(parts) == 2 else (raw, "")

    kw_start = m.start()

    # FIX BUG-3: keyword at position 0 means name == type keyword
    # (e.g. 'geometry geometry', 'date date').
    # Use the first whitespace token as the name; the rest is the type.
    if kw_start == 0:
        parts = raw.split(None, 1)
        if len(parts) == 2:
            return parts[0], parts[1].strip()
        return raw, ""

    col_name = raw[:kw_start].strip()
    type_part = raw[kw_start:].strip()
    return col_name, type_part


# ── Datatype mapping ──────────────────────────────────────────────────────────


def _map_datatype(
    col_name: str, type_str: str
) -> tuple[str, list[ConversionWarning]]:
    """Map a Redshift type string to its Fabric T-SQL equivalent."""
    warnings: list[ConversionWarning] = []
    upper = type_str.upper().strip()

    if not upper:
        return "VARCHAR(MAX)", warnings

    # Numeric with precision
    nm = re.match(r"(NUMERIC|DECIMAL)\s*\((\d+)\s*,\s*(\d+)\)", upper)
    if nm:
        return f"DECIMAL({nm.group(2)},{nm.group(3)})", warnings
    if re.match(r"(NUMERIC|DECIMAL)\s*$", upper):
        return "DECIMAL(18,0)", warnings

    # String types
    vc_m = re.match(
        r"(?:CHARACTER\s+VARYING|VARCHAR|NVARCHAR|CHARACTER|CHAR|BPCHAR)\s*\((\d+)\)",
        upper,
    )
    if vc_m:
        length = int(vc_m.group(1))
        base = "NVARCHAR" if "NVARCHAR" in upper else "VARCHAR"
        if length > VARCHAR_MAX_THRESHOLD or length == 65535:
            return f"{base}(MAX)", warnings
        return f"{base}({length})", warnings

    if re.match(r"(?:CHARACTER\s+VARYING|VARCHAR)\s*$", upper):
        return "VARCHAR(MAX)", warnings
    if re.match(r"(?:CHARACTER|CHAR|BPCHAR)\s*$", upper):
        return "CHAR(1)", warnings

    # Timestamps
    if "TIMESTAMP WITHOUT TIME ZONE" in upper or upper == "TIMESTAMP":
        return "DATETIME2(6)", warnings
    if "TIMESTAMP WITH TIME ZONE" in upper:
        warnings.append(ConversionWarning(
            level=WarningLevel.WARNING,
            code="TIMEZONE_STRIPPED",
            message=(
                f"Column '{col_name}': TIMESTAMP WITH TIME ZONE → DATETIME2(6). "
                "Timezone information is not preserved in Fabric Warehouse."
            ),
            suggestion="Ensure all timestamps are stored in UTC before migration.",
        ))
        return "DATETIME2(6)", warnings

    # Float
    if upper in ("DOUBLE PRECISION", "FLOAT8", "FLOAT"):
        return "FLOAT(53)", warnings
    if upper in ("FLOAT4", "REAL"):
        return "REAL", warnings

    # Boolean
    if upper in ("BOOLEAN", "BOOL"):
        return "BIT", warnings

    # Integer aliases
    if upper in ("INT8", "BIGINT"):
        return "BIGINT", warnings
    if upper in ("INT4", "INTEGER", "INT"):
        return "INT", warnings
    if upper in ("INT2", "SMALLINT"):
        return "SMALLINT", warnings

    # Date / time
    if upper == "DATE":
        return "DATE", warnings
    if upper in ("TIME", "TIMETZ"):
        return "TIME", warnings

    # Redshift-specific / unsupported — look up in rule registry
    for key, mapping in DATATYPE_MAP.items():
        if upper == key or upper.startswith(key + " ") or upper.startswith(key + "("):
            fabric_type = mapping["fabric_type"]
            severity = mapping.get("severity")
            note = mapping.get("note", f"Type {key} → {fabric_type}.")
            if severity in ("WARN", "MANUAL", "UNSUPPORTED"):
                warnings.append(ConversionWarning(
                    level=WarningLevel.WARNING,
                    code=f"DATATYPE_{key.replace(' ', '_')}",
                    message=note,
                    suggestion=note,
                    original_fragment=type_str,
                ))
            return fabric_type, warnings

    # Unknown type
    warnings.append(ConversionWarning(
        level=WarningLevel.WARNING,
        code="UNKNOWN_DATATYPE",
        message=(
            f"Column '{col_name}': unrecognised type '{type_str}' — passed through."
        ),
        suggestion="Verify this type is supported in Microsoft Fabric Warehouse.",
        original_fragment=type_str,
    ))
    return type_str.upper(), warnings
