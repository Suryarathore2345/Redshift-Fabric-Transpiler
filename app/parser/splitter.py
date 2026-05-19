"""
DDL Splitter

Responsible for:
  1. Splitting a multi-statement SQL file into individual DDL statements.
  2. Classifying each statement as TABLE / VIEW / MATERIALIZED_VIEW / etc.
  3. Normalising line endings and stripping BOM characters.

Why not use sqlglot.parse() for splitting?
  sqlglot.parse() is an excellent AST parser but can struggle with very
  non-standard Redshift DDL (ENCODE clauses, DISTSTYLE, etc.).  We split
  first with a simple delimiter-aware tokeniser, then pass individual
  statements to the AST layer for richer analysis.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from app.core.models import ObjectType
from app.logging.logger import get_logger

log = get_logger("splitter")

# ── Delimiter-aware split ────────────────────────────────────────────────────
#
# Statements end at a semicolon that is NOT inside:
#   - a single-quoted string
#   - a double-quoted identifier
#   - a block comment (/* … */)
#   - a line comment (-- …)
#
# We implement a tiny hand-written state machine instead of a regex because
# regex alone cannot handle nested quotes correctly.


class _State(Enum):
    NORMAL = "NORMAL"
    SINGLE_QUOTE = "SINGLE_QUOTE"
    DOUBLE_QUOTE = "DOUBLE_QUOTE"
    LINE_COMMENT = "LINE_COMMENT"
    BLOCK_COMMENT = "BLOCK_COMMENT"


def split_statements(sql: str) -> list[str]:
    """
    Split a SQL file into individual statements delimited by ';'.
    Returns non-empty, stripped statements only.
    """
    # Normalise: strip BOM, normalise CRLF → LF
    sql = sql.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")

    statements: list[str] = []
    buf: list[str] = []
    state = _State.NORMAL
    i = 0
    n = len(sql)

    while i < n:
        ch = sql[i]
        next_ch = sql[i + 1] if i + 1 < n else ""

        if state is _State.NORMAL:
            if ch == "'" :
                state = _State.SINGLE_QUOTE
                buf.append(ch)
            elif ch == '"':
                state = _State.DOUBLE_QUOTE
                buf.append(ch)
            elif ch == "-" and next_ch == "-":
                state = _State.LINE_COMMENT
                buf.append(ch)
            elif ch == "/" and next_ch == "*":
                state = _State.BLOCK_COMMENT
                buf.append(ch)
            elif ch == ";":
                stmt = "".join(buf).strip()
                if stmt:
                    statements.append(stmt)
                buf = []
            else:
                buf.append(ch)

        elif state is _State.SINGLE_QUOTE:
            buf.append(ch)
            if ch == "'" and next_ch == "'":
                # escaped quote inside string
                buf.append(next_ch)
                i += 1
            elif ch == "'":
                state = _State.NORMAL

        elif state is _State.DOUBLE_QUOTE:
            buf.append(ch)
            if ch == '"':
                state = _State.NORMAL

        elif state is _State.LINE_COMMENT:
            buf.append(ch)
            if ch == "\n":
                state = _State.NORMAL

        elif state is _State.BLOCK_COMMENT:
            buf.append(ch)
            if ch == "*" and next_ch == "/":
                buf.append(next_ch)
                i += 1
                state = _State.NORMAL

        i += 1

    # Flush remaining buffer (statement without trailing semicolon)
    remainder = "".join(buf).strip()
    if remainder:
        statements.append(remainder)

    return statements


# ── Statement classifier ─────────────────────────────────────────────────────

# Patterns to detect statement type from the first meaningful tokens
_CREATE_TABLE_RE = re.compile(
    r"^\s*CREATE\s+(?:TEMP(?:ORARY)?\s+)?TABLE\b",
    re.IGNORECASE,
)
_CREATE_MATVIEW_RE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?MATERIALIZED\s+VIEW\b",
    re.IGNORECASE,
)
_CREATE_VIEW_RE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:OR\s+ALTER\s+)?VIEW\b",
    re.IGNORECASE,
)
_CREATE_SCHEMA_RE = re.compile(r"^\s*CREATE\s+SCHEMA\b", re.IGNORECASE)
_DROP_RE = re.compile(r"^\s*DROP\b", re.IGNORECASE)
_ALTER_RE = re.compile(r"^\s*ALTER\b", re.IGNORECASE)
_INSERT_RE = re.compile(r"^\s*INSERT\b", re.IGNORECASE)
_COMMENT_ONLY_RE = re.compile(r"^\s*(--|/\*)", re.IGNORECASE)


def classify_statement(sql: str) -> ObjectType:
    """
    Return the ObjectType for a single DDL statement.

    Strips leading line/block comments before pattern matching so that
    statements like '-- comment\nCREATE TABLE ...' are classified correctly.
    """
    # Strip leading comments and whitespace
    clean = _strip_leading_comments(sql)

    if _CREATE_MATVIEW_RE.match(clean):
        return ObjectType.MATERIALIZED_VIEW
    if _CREATE_VIEW_RE.match(clean):
        return ObjectType.VIEW
    if _CREATE_TABLE_RE.match(clean):
        return ObjectType.TABLE
    if _CREATE_SCHEMA_RE.match(clean):
        return ObjectType.SCHEMA
    return ObjectType.UNKNOWN


def _strip_leading_comments(sql: str) -> str:
    """Remove all leading -- and /* */ comments, returning first real token."""
    s = sql.strip()
    while True:
        if s.startswith("--"):
            # Strip to end of line
            nl = s.find("\n")
            s = s[nl + 1:].lstrip() if nl != -1 else ""
        elif s.startswith("/*"):
            end = s.find("*/")
            s = s[end + 2:].lstrip() if end != -1 else ""
        else:
            break
    return s


@dataclass
class ClassifiedStatement:
    raw_sql: str
    object_type: ObjectType
    index: int   # position in the original file


def classify_all(statements: list[str]) -> list[ClassifiedStatement]:
    """Classify every statement and return typed list."""
    result: list[ClassifiedStatement] = []
    for idx, stmt in enumerate(statements):
        obj_type = classify_statement(stmt)
        if obj_type is not ObjectType.UNKNOWN:
            result.append(ClassifiedStatement(
                raw_sql=stmt,
                object_type=obj_type,
                index=idx,
            ))
        else:
            log.debug("skipping_statement", index=idx, preview=stmt[:60])
    return result
