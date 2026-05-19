"""
Conversion Reporter

Builds structured ConversionReport objects from a BatchConversionResult.
Provides statistics aggregation, per-rule breakdown, and confidence analysis.
Consumed by the output generator and the API response layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.core.models import BatchConversionResult, ConversionResult, ConversionStatus


@dataclass
class RuleStats:
    rule_id: str
    applied_count: int = 0
    objects: list[str] = field(default_factory=list)


@dataclass
class ConversionReport:
    job_id: str
    generated_at: str
    source_filename: str
    duration_ms: float

    total_objects: int = 0
    successful: int = 0
    partial: int = 0
    manual_review: int = 0
    failed: int = 0
    success_rate: float = 0.0
    avg_confidence: float = 0.0

    table_count: int = 0
    view_count: int = 0

    all_warnings: list[dict] = field(default_factory=list)
    rule_stats: list[RuleStats] = field(default_factory=list)
    objects_needing_review: list[str] = field(default_factory=list)
    failed_objects: list[str] = field(default_factory=list)


def build_report(batch: BatchConversionResult, job_id: str) -> ConversionReport:
    """Build a ConversionReport from a BatchConversionResult."""
    all_results: list[ConversionResult] = batch.table_results + batch.view_results

    # Confidence average
    scores = [r.confidence_score for r in all_results if r.confidence_score > 0]
    avg_confidence = round(sum(scores) / len(scores), 3) if scores else 0.0

    # Aggregate warnings
    all_warnings = []
    for r in all_results:
        for w in r.warnings:
            all_warnings.append({
                "object": r.source_name,
                "level": w.level.value,
                "code": w.code,
                "message": w.message,
                "suggestion": w.suggestion,
            })

    # Rule stats
    rule_map: dict[str, RuleStats] = {}
    for r in all_results:
        for rule in r.applied_rules:
            if rule not in rule_map:
                rule_map[rule] = RuleStats(rule_id=rule)
            rule_map[rule].applied_count += 1
            rule_map[rule].objects.append(r.source_name)

    rule_stats = sorted(rule_map.values(), key=lambda x: -x.applied_count)

    # Objects needing manual review / failed
    review_needed = [
        r.source_name for r in all_results
        if r.status in (ConversionStatus.MANUAL_REVIEW, ConversionStatus.UNSUPPORTED)
    ]
    failed_objs = [
        r.source_name for r in all_results
        if r.status == ConversionStatus.FAILED
    ]

    return ConversionReport(
        job_id=job_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_filename=batch.source_filename,
        duration_ms=batch.total_time_ms,
        total_objects=batch.total_objects,
        successful=batch.successful,
        partial=batch.partial,
        manual_review=batch.manual_review,
        failed=batch.failed,
        success_rate=batch.success_rate,
        avg_confidence=avg_confidence,
        table_count=len(batch.table_results),
        view_count=len(batch.view_results),
        all_warnings=all_warnings,
        rule_stats=rule_stats,
        objects_needing_review=review_needed,
        failed_objects=failed_objs,
    )
