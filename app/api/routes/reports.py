"""
Reports API routes.

Endpoints:
  GET  /reports/           — list all conversion report jobs
  GET  /reports/{job_id}   — fetch JSON summary for a job
  GET  /reports/{job_id}/markdown — fetch Markdown report
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from app.core.settings import settings
from app.logging.logger import get_logger

log = get_logger("api.reports")
router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("/", summary="List all conversion report jobs")
async def list_reports() -> list[dict]:
    """Return a list of all job report directories."""
    rep_dir = settings.reports_dir
    if not rep_dir.exists():
        return []

    jobs = []
    for job_dir in sorted(rep_dir.iterdir(), reverse=True):
        if job_dir.is_dir():
            json_file = job_dir / "conversion_summary.json"
            if json_file.exists():
                try:
                    data = json.loads(json_file.read_text(encoding="utf-8"))
                    jobs.append({
                        "job_id": job_dir.name,
                        "source_filename": data.get("source_filename", ""),
                        "generated_at": data.get("generated_at", ""),
                        "total_objects": data.get("statistics", {}).get("total_objects", 0),
                        "success_rate": data.get("statistics", {}).get("success_rate", 0),
                    })
                except Exception:
                    jobs.append({"job_id": job_dir.name})
    return jobs


@router.get("/{job_id}", summary="Fetch JSON summary for a specific job")
async def get_report_json(job_id: str) -> dict:
    json_file = settings.reports_dir / job_id / "conversion_summary.json"
    if not json_file.exists():
        raise HTTPException(status_code=404, detail=f"Report not found: {job_id}")
    return json.loads(json_file.read_text(encoding="utf-8"))


@router.get("/{job_id}/markdown", response_class=PlainTextResponse,
            summary="Fetch Markdown report for a specific job")
async def get_report_markdown(job_id: str) -> str:
    md_file = settings.reports_dir / job_id / "conversion_report.md"
    if not md_file.exists():
        raise HTTPException(status_code=404, detail=f"Markdown report not found: {job_id}")
    return md_file.read_text(encoding="utf-8")
