"""
Output Generator

Responsible for writing conversion results to disk in a structured layout:

  data/outputs/
    <job_id>/
      tables/
        converted_tables.sql        ← all table DDLs in one file
      views/
        converted_views.sql         ← all view DDLs in one file
      combined/
        all_converted.sql           ← single combined output (most useful)
  data/reports/
    <job_id>/
      conversion_report.md          ← human-readable Markdown report
      conversion_summary.json       ← machine-readable JSON summary
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent
from typing import Any

from app.core.models import BatchConversionResult, ConversionResult, ConversionStatus
from app.core.settings import settings
from app.logging.logger import get_logger

log = get_logger("output_generator")

_SEPARATOR = "\n" + "-" * 80 + "\n"


def write_outputs(batch: BatchConversionResult, job_id: str | None = None) -> dict[str, Path]:
    """
    Write all conversion outputs to disk.

    Returns a dict of {label: Path} for all generated files.
    """
    job_id = job_id or str(uuid.uuid4())[:8]
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    job_name = f"{ts}_{job_id}"

    out_base = settings.output_dir / job_name
    rep_base = settings.reports_dir / job_name

    out_base.mkdir(parents=True, exist_ok=True)
    (out_base / "tables").mkdir(exist_ok=True)
    (out_base / "views").mkdir(exist_ok=True)
    (out_base / "combined").mkdir(exist_ok=True)
    rep_base.mkdir(parents=True, exist_ok=True)

    generated: dict[str, Path] = {}

    # ── Table DDL output ─────────────────────────────────────────────────
    if batch.table_results:
        table_path = out_base / "tables" / "converted_tables.sql"
        _write_sql_file(table_path, batch.table_results, section="TABLES")
        generated["tables"] = table_path

    # ── View DDL output ──────────────────────────────────────────────────
    if batch.view_results:
        view_path = out_base / "views" / "converted_views.sql"
        _write_sql_file(view_path, batch.view_results, section="VIEWS")
        generated["views"] = view_path

    # ── Combined output ──────────────────────────────────────────────────
    combined_path = out_base / "combined" / "all_converted.sql"
    _write_combined(combined_path, batch)
    generated["combined"] = combined_path

    # ── Markdown report ──────────────────────────────────────────────────
    report_md_path = rep_base / "conversion_report.md"
    _write_markdown_report(report_md_path, batch, job_name)
    generated["report_md"] = report_md_path

    # ── JSON summary ─────────────────────────────────────────────────────
    report_json_path = rep_base / "conversion_summary.json"
    _write_json_summary(report_json_path, batch, job_name)
    generated["report_json"] = report_json_path

    batch.output_filename = str(combined_path)
    batch.report_filename = str(report_md_path)

    log.info(
        "outputs_written",
        job=job_name,
        files=list(generated.keys()),
    )
    return generated


# ── File writers ──────────────────────────────────────────────────────────────


def _write_sql_file(path: Path, results: list[ConversionResult], section: str) -> None:
    lines: list[str] = [
        f"-- {'=' * 70}",
        f"-- SECTION: {section}",
        f"-- Generated: {datetime.now(timezone.utc).isoformat()}",
        f"-- {'=' * 70}",
        "",
    ]

    for r in results:
        if not r.output_sql:
            continue
        # NOTE: r.output_sql already contains the rich ══ header block
        # written by table_generator / view_transformer. Do NOT add a
        # duplicate "-- Object:" header here.
        lines.append(r.output_sql)
        lines.append("")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _write_combined(path: Path, batch: BatchConversionResult) -> None:
    lines: list[str] = [
        "-- " + "=" * 78,
        f"-- Redshift → Microsoft Fabric DDL Conversion Output",
        f"-- Source:    {batch.source_filename}",
        f"-- Generated: {datetime.now(timezone.utc).isoformat()}",
        f"-- Objects:   {batch.total_objects} total | "
        f"{batch.successful} high-confidence | "
        f"{batch.partial} partial | "
        f"{batch.manual_review} manual review | "
        f"{batch.failed} failed",
        "-- " + "=" * 78,
        "",
    ]

    # Tables first
    if batch.table_results:
        lines += [
            "-- " + "-" * 78,
            "-- TABLES",
            "-- " + "-" * 78,
            "",
        ]
        for r in batch.table_results:
            if r.output_sql:
                # output_sql already has the full ══ header — write directly
                lines.append(r.output_sql)
                lines.append("")

    # Views / procedures
    if batch.view_results:
        lines += [
            "",
            "-- " + "-" * 78,
            "-- VIEWS / STORED PROCEDURES",
            "-- " + "-" * 78,
            "",
        ]
        for r in batch.view_results:
            if r.output_sql:
                # output_sql already has the full ══ header — write directly
                lines.append(r.output_sql)
                lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _write_markdown_report(path: Path, batch: BatchConversionResult, job_name: str) -> None:
    success_pct = f"{batch.success_rate:.0%}"
    lines: list[str] = [
        f"# Redshift → Fabric Conversion Report",
        f"",
        f"**Job ID:** `{job_name}`  ",
        f"**Source:** `{batch.source_filename}`  ",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}  ",
        f"**Duration:** {batch.total_time_ms:.0f} ms  ",
        f"",
        f"---",
        f"",
        f"## Summary",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total Objects | {batch.total_objects} |",
        f"| ✅ High Confidence | {batch.successful} |",
        f"| ⚠️ Partial Conversion | {batch.partial} |",
        f"| 🔍 Manual Review Required | {batch.manual_review} |",
        f"| ❌ Failed | {batch.failed} |",
        f"| Success Rate | {success_pct} |",
        f"",
        f"---",
        f"",
    ]

    # Tables section
    if batch.table_results:
        lines += [
            f"## Tables ({len(batch.table_results)})",
            f"",
            f"| Table | Status | Confidence | Warnings |",
            f"|-------|--------|------------|----------|",
        ]
        for r in batch.table_results:
            badge = _status_badge(r.status)
            warn_count = len(r.warnings)
            lines.append(
                f"| `{r.source_name}` | {badge} {r.status.value} "
                f"| {r.confidence_score:.0%} | {warn_count} |"
            )
        lines.append("")

    # Views section
    if batch.view_results:
        lines += [
            f"## Views / Stored Procedures ({len(batch.view_results)})",
            f"",
            f"| View | Status | Confidence | Warnings |",
            f"|------|--------|------------|----------|",
        ]
        for r in batch.view_results:
            badge = _status_badge(r.status)
            warn_count = len(r.warnings)
            lines.append(
                f"| `{r.source_name}` | {badge} {r.status.value} "
                f"| {r.confidence_score:.0%} | {warn_count} |"
            )
        lines.append("")

    # Warnings detail
    all_results = batch.table_results + batch.view_results
    objects_with_warnings = [r for r in all_results if r.warnings]
    if objects_with_warnings:
        lines += [
            f"---",
            f"",
            f"## Warning Details",
            f"",
        ]
        for r in objects_with_warnings:
            lines.append(f"### `{r.source_name}`")
            lines.append(f"")
            for w in r.warnings:
                icon = "❌" if w.level.value == "ERROR" else "⚠️"
                lines.append(f"- {icon} **{w.code}**: {w.message}")
                if w.suggestion:
                    lines.append(f"  - 💡 *{w.suggestion}*")
            lines.append("")

    # Applied rules summary
    all_rules: dict[str, int] = {}
    for r in all_results:
        for rule in r.applied_rules:
            all_rules[rule] = all_rules.get(rule, 0) + 1

    if all_rules:
        lines += [
            f"---",
            f"",
            f"## Applied Transformation Rules",
            f"",
            f"| Rule | Applied Count |",
            f"|------|---------------|",
        ]
        for rule, count in sorted(all_rules.items(), key=lambda x: -x[1]):
            lines.append(f"| `{rule}` | {count} |")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _write_json_summary(path: Path, batch: BatchConversionResult, job_name: str) -> None:
    def _result_to_dict(r: ConversionResult) -> dict:
        return {
            "source_name": r.source_name,
            "target_name": r.target_name,
            "object_type": r.object_type.value,
            "status": r.status.value,
            "confidence_score": r.confidence_score,
            "warnings_count": len(r.warnings),
            "warnings": [
                {
                    "level": w.level.value,
                    "code": w.code,
                    "message": w.message,
                    "suggestion": w.suggestion,
                }
                for w in r.warnings
            ],
            "applied_rules": r.applied_rules,
            "unsupported_features": r.unsupported_features,
            "manual_review_items": r.manual_review_items,
            "transform_time_ms": round(r.transform_time_ms, 2),
        }

    summary: dict[str, Any] = {
        "job_id": job_name,
        "source_filename": batch.source_filename,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": round(batch.total_time_ms, 1),
        "statistics": {
            "total_objects": batch.total_objects,
            "successful": batch.successful,
            "partial": batch.partial,
            "manual_review": batch.manual_review,
            "failed": batch.failed,
            "success_rate": batch.success_rate,
        },
        "tables": [_result_to_dict(r) for r in batch.table_results],
        "views": [_result_to_dict(r) for r in batch.view_results],
    }

    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def _status_badge(status: ConversionStatus) -> str:
    return {
        ConversionStatus.HIGH_CONFIDENCE: "✅",
        ConversionStatus.PARTIAL: "⚠️",
        ConversionStatus.MANUAL_REVIEW: "🔍",
        ConversionStatus.FAILED: "❌",
        ConversionStatus.UNSUPPORTED: "🚫",
    }.get(status, "❓")
