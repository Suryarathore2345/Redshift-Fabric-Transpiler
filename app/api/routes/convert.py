"""
Conversion API routes.

Endpoints:
  POST /convert/sql     — convert inline SQL string
  POST /convert/file    — upload a .sql file and convert
  POST /convert/validate — validate already-converted T-SQL for residual Redshift syntax
  GET  /convert/download/{job_id} — download combined output SQL
"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse

from app.api.schemas import (
    BatchSummarySchema,
    ConversionResultSchema,
    ConvertSQLRequest,
    ValidateRequest,
    ValidateResponse,
    WarningSchema,
)
from app.core.models import ConversionResult, ConversionWarning
from app.core.pipeline import convert_sql
from app.core.settings import settings
from app.logging.logger import get_logger
from app.output.generator import write_outputs
from app.validator.validator import validate_result
from app.core.models import ConversionResult, ConversionStatus, ObjectType

log = get_logger("api.convert")
router = APIRouter(prefix="/convert", tags=["Conversion"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _w(w: ConversionWarning) -> WarningSchema:
    return WarningSchema(
        level=w.level.value,
        code=w.code,
        message=w.message,
        suggestion=w.suggestion,
    )


def _r(r: ConversionResult) -> ConversionResultSchema:
    return ConversionResultSchema(
        source_name=r.source_name,
        target_name=r.target_name,
        object_type=r.object_type.value,
        status=r.status.value,
        confidence_score=r.confidence_score,
        output_sql=r.output_sql,
        warnings=[_w(w) for w in r.warnings],
        applied_rules=r.applied_rules,
        unsupported_features=r.unsupported_features,
        manual_review_items=r.manual_review_items,
        transform_time_ms=round(r.transform_time_ms, 2),
    )


def _build_response(batch, generated: dict) -> BatchSummarySchema:
    return BatchSummarySchema(
        source_filename=batch.source_filename,
        total_objects=batch.total_objects,
        successful=batch.successful,
        partial=batch.partial,
        manual_review=batch.manual_review,
        failed=batch.failed,
        success_rate=batch.success_rate,
        total_time_ms=round(batch.total_time_ms, 1),
        tables=[_r(r) for r in batch.table_results],
        views=[_r(r) for r in batch.view_results],
        output_files={k: str(v) for k, v in generated.items()},
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post(
    "/sql",
    response_model=BatchSummarySchema,
    summary="Convert inline Redshift DDL SQL to Fabric T-SQL",
)
async def convert_sql_endpoint(request: ConvertSQLRequest) -> BatchSummarySchema:
    """
    Accept raw Redshift DDL SQL and return converted Fabric T-SQL with full diagnostics.
    """
    if not request.sql.strip():
        raise HTTPException(status_code=400, detail="SQL input is empty.")

    job_id = str(uuid.uuid4())[:8]
    log.info("convert_sql_request", filename=request.source_filename, job_id=job_id)

    batch = convert_sql(
        request.sql,
        source_filename=request.source_filename,
        mv_target=request.mv_target,
        schema_mode=request.schema_mode,
    )
    generated = write_outputs(batch, job_id=job_id)

    return _build_response(batch, generated)


@router.post(
    "/file",
    response_model=BatchSummarySchema,
    summary="Upload a Redshift DDL .sql file and convert",
)
async def convert_file_endpoint(
    file: UploadFile = File(...),
    mv_target: str = "warehouse_sp",
    schema_mode: str = "dynamic",
) -> BatchSummarySchema:
    """
    Upload a `.sql` file containing Redshift DDL.
    Returns converted Fabric T-SQL with full diagnostics.
    """
    if not file.filename.endswith(".sql"):
        raise HTTPException(status_code=400, detail="Only .sql files are accepted.")

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {settings.max_upload_size_mb} MB.",
        )

    # Save upload
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    upload_path = settings.upload_dir / file.filename
    upload_path.write_bytes(content)

    sql = content.decode("utf-8-sig")  # strip BOM if present
    job_id = str(uuid.uuid4())[:8]
    log.info("convert_file_request", filename=file.filename, size=len(content), job_id=job_id)

    batch = convert_sql(sql, source_filename=file.filename, mv_target=mv_target, schema_mode=schema_mode)
    generated = write_outputs(batch, job_id=job_id)

    return _build_response(batch, generated)


@router.post(
    "/validate",
    response_model=ValidateResponse,
    summary="Validate converted T-SQL for residual Redshift syntax",
)
async def validate_sql_endpoint(request: ValidateRequest) -> ValidateResponse:
    """
    Run the validation engine against already-converted Fabric T-SQL.
    Returns any residual Redshift clauses or unsupported patterns found.
    """
    from app.core.models import ConversionResult, ConversionStatus, ObjectType
    dummy = ConversionResult(
        source_name="inline",
        target_name="inline",
        object_type=ObjectType.UNKNOWN,
        status=ConversionStatus.HIGH_CONFIDENCE,
        confidence_score=1.0,
        output_sql=request.sql,
    )
    validated = validate_result(dummy)
    new_warnings = [_w(w) for w in validated.warnings]
    return ValidateResponse(
        warnings=new_warnings,
        is_clean=len(new_warnings) == 0,
        residual_issues_count=len(new_warnings),
    )


@router.get(
    "/download/{filename:path}",
    summary="Download a generated output SQL file",
)
async def download_output(filename: str):
    """
    Download a previously generated conversion output file by its relative path.
    """
    target = settings.output_dir / filename
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    return FileResponse(
        path=str(target),
        media_type="text/plain",
        filename=target.name,
    )
