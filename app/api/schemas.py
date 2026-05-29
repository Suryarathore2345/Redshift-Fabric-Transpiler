"""
API request/response schemas (Pydantic v2).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ── Request schemas ───────────────────────────────────────────────────────────


class ConvertSQLRequest(BaseModel):
    sql: str = Field(..., description="Raw Redshift DDL SQL (one or many statements)")
    source_filename: str = Field("inline_input.sql", description="Logical filename for reporting")
    mv_target: str = Field(
        "warehouse_sp",
        description=(
            "Target for Materialized Views: "
            "'warehouse_sp' = Fabric Warehouse Stored Procedure (T-SQL CTAS pattern), "
            "'lakehouse_mv' = Fabric Lakehouse Materialized Lake View (Spark SQL)"
        ),
    )
    schema_mode: str = Field(
        "dynamic",
        description=(
            "Schema output mode: "
            "'dynamic' = parameterised placeholders like ${rs_bi_alefdw} (default), "
            "'hardcoded' = keep original schema names as-is"
        ),
    )

    model_config = {"json_schema_extra": {
        "example": {
            "sql": (
                "CREATE TABLE bi_alefdw.student_login (\n"
                "    login_date_dw_id bigint ENCODE raw,\n"
                "    school_dw_id     bigint ENCODE raw DISTKEY,\n"
                "    outside_school_flag boolean ENCODE raw\n"
                ") DISTSTYLE AUTO SORTKEY (school_dw_id, login_date_dw_id);\n"
            ),
            "source_filename": "bi_alefdw_tables.sql",
            "mv_target": "warehouse_sp",
            "schema_mode": "dynamic",
        }
    }}


# ── Response schemas ──────────────────────────────────────────────────────────


class WarningSchema(BaseModel):
    level: str
    code: str
    message: str
    suggestion: str = ""


class ConversionResultSchema(BaseModel):
    source_name: str
    target_name: str
    object_type: str
    status: str
    confidence_score: float
    output_sql: str
    warnings: list[WarningSchema]
    applied_rules: list[str]
    unsupported_features: list[str]
    manual_review_items: list[str]
    transform_time_ms: float


class BatchSummarySchema(BaseModel):
    source_filename: str
    total_objects: int
    successful: int
    partial: int
    manual_review: int
    failed: int
    success_rate: float
    total_time_ms: float
    tables: list[ConversionResultSchema]
    views: list[ConversionResultSchema]
    output_files: dict[str, str] = {}


class ValidateRequest(BaseModel):
    sql: str = Field(..., description="Already-converted Fabric T-SQL to validate")


class ValidateResponse(BaseModel):
    warnings: list[WarningSchema]
    is_clean: bool
    residual_issues_count: int
