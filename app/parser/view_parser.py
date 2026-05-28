"""
View DDL Parser

Parses Redshift CREATE [OR REPLACE] VIEW and CREATE MATERIALIZED VIEW
statements into a ViewIR object.

The parser:
  1. Extracts schema, object name, type, flags.
  2. Extracts the raw SELECT body.
  3. Catalogues CTEs, schema references, and table references.
  4. Does NOT transform the body — that is the Transformer's responsibility.
"""
from __future__ import annotations

import re
from typing import Optional

from app.core.models import (
    ConversionWarning,
    ObjectType,
    WarningLevel,
    ViewIR,
)
from app.logging.logger import get_logger

log = get_logger("view_parser")

# ── Header patterns ───────────────────────────────────────────────────────────

_MATVIEW_HEADER = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?MATERIALIZED\s+VIEW\s+"
    r"([^\s(]+)"                        # schema.name
    r"(?:\s+BACKUP\s+\w+)?"            # optional BACKUP clause
    r"(?:\s+DISTSTYLE\s+\w+)?"         # optional DISTSTYLE
    r"(?:\s+DISTKEY\s*\([^)]*\))?"     # optional DISTKEY
    r"(?:\s+SORTKEY\s*\([^)]*\))?"     # optional SORTKEY
    r"\s+AS\s*\(?",                    # AS (optional leading paren)
    re.IGNORECASE | re.DOTALL,
)

_VIEW_HEADER = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+"
    r"([^\s(]+)"                        # schema.name
    r"(?:\s+WITH\s+NO\s+SCHEMA\s+BINDING)?"
    r"\s+AS\s*",
    re.IGNORECASE | re.DOTALL,
)

_OR_REPLACE_RE = re.compile(r"CREATE\s+OR\s+REPLACE\b", re.IGNORECASE)
_NO_SCHEMA_BINDING_RE = re.compile(r"WITH\s+NO\s+SCHEMA\s+BINDING", re.IGNORECASE)

# CTE extraction
_CTE_NAME_RE = re.compile(r"\bWITH\s+(\w+)\s+AS\s*\(", re.IGNORECASE)

# Schema references — "schema.table" pattern
_SCHEMA_TABLE_RE = re.compile(r"\b([\w]+)\.([\w]+)\b")


def parse_view(sql: str) -> ViewIR:
    """
    Parse a Redshift VIEW or MATERIALIZED VIEW statement into a ViewIR.

    Raises:
        ValueError: if the statement cannot be recognised.
    """
    is_matview = bool(re.search(r"\bMATERIALIZED\s+VIEW\b", sql, re.IGNORECASE))
    object_type = ObjectType.MATERIALIZED_VIEW if is_matview else ObjectType.VIEW
    is_create_or_replace = bool(_OR_REPLACE_RE.search(sql))
    has_no_schema_binding = bool(_NO_SCHEMA_BINDING_RE.search(sql))

    # ── Extract schema.name ───────────────────────────────────────────────
    header_re = _MATVIEW_HEADER if is_matview else _VIEW_HEADER
    header_m = header_re.search(sql)
    if not header_m:
        raise ValueError(f"Cannot parse view header from: {sql[:120]!r}")

    full_name = header_m.group(1).strip().strip('"')
    if "." in full_name:
        schema, name = full_name.rsplit(".", 1)
    else:
        schema, name = "", full_name

    # ── Extract body ──────────────────────────────────────────────────────
    body_start = header_m.end()
    body = sql[body_start:].strip()

    # Materialised views may wrap their body in parens: MV AS( ... )
    # Strip leading/trailing parens if the entire body is parenthesised
    if is_matview and body.startswith("("):
        body = _strip_outer_parens(body)

    # Strip trailing WITH NO SCHEMA BINDING
    body = _NO_SCHEMA_BINDING_RE.sub("", body).strip().rstrip(";").strip()

    # ── Discover CTE names ────────────────────────────────────────────────
    cte_names = _CTE_NAME_RE.findall(body)

    # ── Discover schema/table references ─────────────────────────────────
    referenced_schemas: set[str] = set()
    referenced_tables: list[str] = []
    for m in _SCHEMA_TABLE_RE.finditer(body):
        schema_ref = m.group(1).lower()
        table_ref = m.group(2)
        referenced_schemas.add(schema_ref)
        referenced_tables.append(f"{schema_ref}.{table_ref}")

    warnings: list[ConversionWarning] = []

    if is_matview:
        warnings.append(ConversionWarning(
            level=WarningLevel.WARNING,
            code="MATERIALIZED_VIEW",
            message=(
                f"'{schema}.{name}' is a MATERIALIZED VIEW. "
                "Fabric Warehouse does not support materialised views natively."
            ),
            suggestion=(
                "Convert to a stored procedure (usp_refresh_<name>) using the "
                "CTAS pattern: DROP TABLE IF EXISTS + CREATE TABLE … AS SELECT …"
            ),
        ))

    return ViewIR(
        schema=schema,
        name=name,
        object_type=object_type,
        body=body,
        is_create_or_replace=is_create_or_replace,
        has_no_schema_binding=has_no_schema_binding,
        cte_names=cte_names,
        referenced_schemas=list(referenced_schemas),
        referenced_tables=referenced_tables,
        warnings=warnings,
    )


def _strip_outer_parens(s: str) -> str:
    """
    If the string starts with '(' and the matching ')' is at or near the end,
    strip them.  Used to unwrap MATERIALIZED VIEW bodies.
    """
    if not s.startswith("("):
        return s
    depth = 0
    for i, ch in enumerate(s):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                inner = s[1:i].strip()
                # Only strip if the closing ) is at or very near the end
                trailing = s[i + 1:].strip()
                if not trailing or trailing in (";", ""):
                    return inner
                break
    return s
