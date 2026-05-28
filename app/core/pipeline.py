"""
Conversion Pipeline

Central orchestrator that:
  1. Splits raw SQL into individual statements
  2. Classifies each statement (TABLE / VIEW / MATERIALIZED_VIEW)
  3. Parses each into an IR
  4. Transforms each IR into Fabric T-SQL
  5. Validates the output
  6. Aggregates results into a BatchConversionResult

This is the single entry point called by the API layer AND the CLI runner.
"""
from __future__ import annotations

import time
from pathlib import Path

from app.core.models import (
    BatchConversionResult,
    ConversionResult,
    ConversionStatus,
    ConversionWarning,
    ObjectType,
    WarningLevel,
)
from app.logging.logger import get_logger
from app.parser.splitter import ClassifiedStatement, classify_all, split_statements
from app.parser.table_parser import parse_table
from app.parser.view_parser import parse_view
from app.transformer.table_generator import generate_table
from app.transformer.view_transformer import transform_view
from app.validator.validator import validate_result

log = get_logger("pipeline")


def convert_sql(
    sql: str,
    source_filename: str = "input.sql",
) -> BatchConversionResult:
    """
    Full conversion pipeline for a raw SQL string.

    Args:
        sql:              Raw Redshift DDL SQL (may contain multiple statements).
        source_filename:  Logical filename for reporting.

    Returns:
        BatchConversionResult with all table and view results.
    """
    t0 = time.perf_counter()
    log.info("pipeline_start", source=source_filename)

    batch = BatchConversionResult(source_filename=source_filename)

    # ── 1. Split into individual statements ───────────────────────────────
    try:
        raw_statements = split_statements(sql)
    except Exception as exc:
        log.error("split_failed", error=str(exc))
        batch.failed += 1
        return batch

    log.info("statements_found", count=len(raw_statements))

    # ── 2. Classify statements ────────────────────────────────────────────
    classified: list[ClassifiedStatement] = classify_all(raw_statements)
    log.info("classified", count=len(classified))

    # ── 3. Process each statement ─────────────────────────────────────────
    for stmt in classified:
        result = _process_statement(stmt)
        _register_result(batch, result)

    # ── 4. Compute batch totals ───────────────────────────────────────────
    batch.total_objects = len(classified)
    batch.total_time_ms = (time.perf_counter() - t0) * 1000

    log.info(
        "pipeline_complete",
        source=source_filename,
        total=batch.total_objects,
        successful=batch.successful,
        partial=batch.partial,
        manual_review=batch.manual_review,
        failed=batch.failed,
        duration_ms=round(batch.total_time_ms, 1),
    )

    return batch


def _process_statement(stmt: ClassifiedStatement) -> ConversionResult:
    """Parse, transform, and validate a single classified DDL statement."""
    try:
        if stmt.object_type == ObjectType.TABLE:
            ir = parse_table(stmt.raw_sql)
            result = generate_table(ir, source_sql=stmt.raw_sql)

        elif stmt.object_type in (ObjectType.VIEW, ObjectType.MATERIALIZED_VIEW):
            ir = parse_view(stmt.raw_sql)
            result = transform_view(ir, source_sql=stmt.raw_sql)

        else:
            return ConversionResult(
                source_name=f"unknown_{stmt.index}",
                target_name="",
                object_type=stmt.object_type,
                status=ConversionStatus.FAILED,
                confidence_score=0.0,
                source_sql=stmt.raw_sql,
                output_sql="",
                warnings=[ConversionWarning(
                    level=WarningLevel.WARNING,
                    code="UNSUPPORTED_STATEMENT_TYPE",
                    message=f"Statement type {stmt.object_type} is not supported.",
                )],
            )

        # Run post-conversion validation
        result = validate_result(result)
        return result

    except Exception as exc:
        log.error(
            "statement_conversion_failed",
            index=stmt.index,
            type=stmt.object_type,
            error=str(exc),
            preview=stmt.raw_sql[:80],
        )
        return ConversionResult(
            source_name=f"error_{stmt.index}",
            target_name="",
            object_type=stmt.object_type,
            status=ConversionStatus.FAILED,
            confidence_score=0.0,
            source_sql=stmt.raw_sql,
            output_sql="",
            warnings=[ConversionWarning(
                level=WarningLevel.ERROR,
                code="PARSE_ERROR",
                message=f"Failed to convert statement: {exc}",
                suggestion="Review original SQL and fix syntax issues before retrying.",
            )],
        )


def _register_result(batch: BatchConversionResult, result: ConversionResult) -> None:
    """Append result to the right bucket and increment counters."""
    if result.object_type == ObjectType.TABLE:
        batch.table_results.append(result)
    else:
        batch.view_results.append(result)

    if result.status == ConversionStatus.HIGH_CONFIDENCE:
        batch.successful += 1
    elif result.status == ConversionStatus.PARTIAL:
        batch.partial += 1
    elif result.status == ConversionStatus.MANUAL_REVIEW:
        batch.manual_review += 1
    else:
        batch.failed += 1
