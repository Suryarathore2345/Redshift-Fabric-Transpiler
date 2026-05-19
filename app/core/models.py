"""
Intermediate Representation (IR) models.

Every DDL object — table, view, column, constraint — is first parsed into
these Pydantic models before any dialect-specific code is emitted.
This decouples parsing from generation and enables multi-target output
(Fabric, Synapse, Snowflake …) without touching the parser layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enums ────────────────────────────────────────────────────────────────────


class ObjectType(str, Enum):
    TABLE = "TABLE"
    VIEW = "VIEW"
    MATERIALIZED_VIEW = "MATERIALIZED_VIEW"
    PROCEDURE = "PROCEDURE"
    SCHEMA = "SCHEMA"
    UNKNOWN = "UNKNOWN"


class ConversionStatus(str, Enum):
    HIGH_CONFIDENCE = "HIGH_CONFIDENCE"
    PARTIAL = "PARTIAL"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    UNSUPPORTED = "UNSUPPORTED"
    FAILED = "FAILED"


class WarningLevel(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


# ── Column IR ────────────────────────────────────────────────────────────────


@dataclass
class ColumnIR:
    """Representation of a single column definition."""

    name: str
    original_type: str                  # raw Redshift type string
    fabric_type: str = ""               # resolved Fabric T-SQL type
    is_nullable: bool = True
    default_value: str | None = None
    is_identity: bool = False
    encode: str | None = None           # Redshift ENCODE clause (stripped)
    is_distkey: bool = False            # Redshift DISTKEY (stripped)
    is_reserved_word: bool = False      # needs [] quoting in T-SQL
    contains_spaces: bool = False       # needs [] quoting
    warnings: list[ConversionWarning] = field(default_factory=list)


# ── Table IR ─────────────────────────────────────────────────────────────────


@dataclass
class TableIR:
    """Representation of a CREATE TABLE statement."""

    schema: str
    name: str
    columns: list[ColumnIR] = field(default_factory=list)

    # Redshift-specific (all stripped from Fabric output)
    diststyle: str | None = None
    distkey: str | None = None
    sortkeys: list[str] = field(default_factory=list)
    sortkey_type: str | None = None     # COMPOUND | INTERLEAVED
    backup: bool = True
    encode_default: str | None = None

    # Conversion metadata
    warnings: list[ConversionWarning] = field(default_factory=list)
    status: ConversionStatus = ConversionStatus.HIGH_CONFIDENCE
    confidence_score: float = 1.0


# ── View IR ──────────────────────────────────────────────────────────────────


@dataclass
class ViewIR:
    """Representation of a CREATE [MATERIALIZED] VIEW statement."""

    schema: str
    name: str
    object_type: ObjectType                 # VIEW or MATERIALIZED_VIEW
    body: str                               # original SQL body (SELECT …)
    transformed_body: str = ""             # Fabric-ready SQL body
    is_create_or_replace: bool = False
    has_no_schema_binding: bool = False     # Redshift-only; stripped

    cte_names: list[str] = field(default_factory=list)
    referenced_schemas: list[str] = field(default_factory=list)
    referenced_tables: list[str] = field(default_factory=list)

    warnings: list[ConversionWarning] = field(default_factory=list)
    status: ConversionStatus = ConversionStatus.HIGH_CONFIDENCE
    confidence_score: float = 1.0


# ── Warning IR ───────────────────────────────────────────────────────────────


@dataclass
class ConversionWarning:
    level: WarningLevel
    code: str               # e.g. "UNSUPPORTED_DISTKEY", "LISTAGG_PARTIAL"
    message: str
    suggestion: str = ""
    line_hint: int | None = None
    original_fragment: str = ""


# ── Conversion Result ─────────────────────────────────────────────────────────


@dataclass
class ConversionResult:
    """Final output produced by the conversion pipeline for one DDL object."""

    source_name: str               # original schema.object
    target_name: str               # Fabric parameterised name
    object_type: ObjectType
    status: ConversionStatus
    confidence_score: float

    source_sql: str = ""
    output_sql: str = ""

    warnings: list[ConversionWarning] = field(default_factory=list)
    applied_rules: list[str] = field(default_factory=list)
    unsupported_features: list[str] = field(default_factory=list)
    manual_review_items: list[str] = field(default_factory=list)

    # Timing
    parse_time_ms: float = 0.0
    transform_time_ms: float = 0.0


# ── Batch Result ─────────────────────────────────────────────────────────────


@dataclass
class BatchConversionResult:
    """Aggregated result for a full file / batch conversion."""

    source_filename: str
    total_objects: int = 0
    successful: int = 0
    partial: int = 0
    manual_review: int = 0
    failed: int = 0

    table_results: list[ConversionResult] = field(default_factory=list)
    view_results: list[ConversionResult] = field(default_factory=list)

    total_time_ms: float = 0.0
    output_filename: str = ""
    report_filename: str = ""

    @property
    def success_rate(self) -> float:
        if self.total_objects == 0:
            return 0.0
        return round(self.successful / self.total_objects, 4)
