#!/usr/bin/env python3
"""
run.py  ──  Direct entry point for the Redshift → Fabric DDL Converter

Usage
─────
  # Convert a SQL file (most common)
  python run.py convert --file path/to/bi_alefdw_tables.sql

  # Convert inline SQL from a string
  python run.py convert --sql "CREATE TABLE bi_alefdw.foo (id bigint ENCODE az64) DISTSTYLE AUTO;"

  # Convert SQL and write outputs to a custom folder
  python run.py convert --file my.sql --out-dir ./results

  # Validate already-converted Fabric T-SQL for residual Redshift syntax
  python run.py validate --file path/to/converted.sql

  # Start the FastAPI REST API server
  python run.py server

  # Start the server on a custom port
  python run.py server --port 9000

  # Run all unit tests
  python run.py test

  # Run only integration tests
  python run.py test --suite integration

Architecture
────────────
  run.py orchestrates these internal modules:

  app.core.settings    ─ centralised config (placeholders, paths, thresholds)
  app.core.pipeline    ─ convert_sql(): the master orchestration function
  app.core.models      ─ all IR/result dataclasses
  app.parser.splitter  ─ splits raw SQL into individual statements
  app.parser.table_parser  ─ parses CREATE TABLE → TableIR
  app.parser.view_parser   ─ parses CREATE VIEW → ViewIR
  app.transformer.table_generator  ─ TableIR → Fabric T-SQL CREATE TABLE
  app.transformer.view_transformer ─ ViewIR  → Fabric T-SQL CREATE OR ALTER VIEW
  app.validator.validator          ─ post-conversion residual Redshift checks
  app.output.generator             ─ writes .sql output files + reports
  app.reporter.reporter            ─ builds ConversionReport objects
  app.api.app                      ─ FastAPI application factory
"""
from __future__ import annotations

import argparse
import sys
import textwrap
import time
import uuid
from pathlib import Path

# ── Bootstrap: ensure project root is on sys.path ─────────────────────────────
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Internal imports (after sys.path fix) ─────────────────────────────────────
from app.core.settings import settings, ensure_directories
from app.logging.logger import configure_logging, get_logger

# Configure logging before anything else so all module-level loggers pick it up
configure_logging(level=settings.log_level, log_dir=settings.logs_dir)
log = get_logger("run")


# ══════════════════════════════════════════════════════════════════════════════
#  COMMAND: convert
# ══════════════════════════════════════════════════════════════════════════════

def cmd_convert(args: argparse.Namespace) -> int:
    """
    Run the full conversion pipeline on a SQL file or inline SQL string.
    Returns exit code (0 = success, 1 = partial/errors, 2 = all failed).
    """
    from app.core.pipeline import convert_sql
    from app.output.generator import write_outputs
    from app.reporter.reporter import build_report
    from app.core.models import ConversionStatus

    # ── Resolve SQL input ─────────────────────────────────────────────────
    if args.file:
        sql_path = Path(args.file)
        if not sql_path.exists():
            print(f"[ERROR] File not found: {sql_path}", file=sys.stderr)
            return 2
        sql = sql_path.read_text(encoding="utf-8-sig")
        source_name = sql_path.name
        print(f"\n📂  Source file : {sql_path.resolve()}")
    elif args.sql:
        sql = args.sql
        source_name = "inline_input.sql"
        print(f"\n📝  Source      : inline SQL ({len(sql)} chars)")
    else:
        print("[ERROR] Provide --file or --sql", file=sys.stderr)
        return 2

    print(f"🎯  Target      : Microsoft Fabric T-SQL")
    print(f"🔧  Mode        : {'Tables + Views' if not args.tables_only and not args.views_only else ('Tables only' if args.tables_only else 'Views only')}")
    print()

    # ── Run pipeline ──────────────────────────────────────────────────────
    t0 = time.perf_counter()
    batch = convert_sql(sql, source_filename=source_name)
    elapsed = (time.perf_counter() - t0) * 1000

    # ── Override output dir if requested ──────────────────────────────────
    if args.out_dir:
        settings.output_dir  = Path(args.out_dir) / "outputs"
        settings.reports_dir = Path(args.out_dir) / "reports"
        ensure_directories()

    # ── Write output files ────────────────────────────────────────────────
    job_id = str(uuid.uuid4())[:8]
    generated = write_outputs(batch, job_id=job_id)

    # ── Build report ──────────────────────────────────────────────────────
    report = build_report(batch, job_id=job_id)

    # ── Print summary to terminal ─────────────────────────────────────────
    _print_conversion_summary(batch, report, generated, elapsed)

    # ── Determine exit code ───────────────────────────────────────────────
    if batch.failed == batch.total_objects:
        return 2   # all failed
    if batch.failed > 0 or batch.manual_review > 0:
        return 1   # partial
    return 0       # clean


def _print_conversion_summary(batch, report, generated, elapsed_ms):
    """Pretty-print conversion summary to stdout."""
    from app.core.models import ConversionStatus

    sep  = "─" * 68
    sep2 = "═" * 68

    print(sep2)
    print("  ✅  CONVERSION COMPLETE")
    print(sep2)
    print(f"  Source       : {batch.source_filename}")
    print(f"  Duration     : {elapsed_ms:.0f} ms")
    print(f"  Total objects: {batch.total_objects}")
    print(sep)
    print(f"  ✅ High confidence : {batch.successful:>4}")
    print(f"  ⚠️  Partial         : {batch.partial:>4}")
    print(f"  🔍 Manual review   : {batch.manual_review:>4}")
    print(f"  ❌ Failed          : {batch.failed:>4}")
    print(f"  📈 Success rate    : {batch.success_rate:.0%}")
    print(f"  🎯 Avg confidence  : {report.avg_confidence:.0%}")
    print(sep)

    # Output files
    print("  📁 Output files:")
    for label, path in generated.items():
        print(f"     {label:<12} → {path}")
    print(sep)

    # Warnings summary
    if report.all_warnings:
        print(f"  ⚠️  {len(report.all_warnings)} warning(s) across {batch.total_objects} objects.")
        by_code: dict[str, int] = {}
        for w in report.all_warnings:
            by_code[w["code"]] = by_code.get(w["code"], 0) + 1
        for code, cnt in sorted(by_code.items(), key=lambda x: -x[1])[:8]:
            print(f"     {code:<40} ×{cnt}")

    # Objects needing manual review
    if report.objects_needing_review:
        print()
        print(f"  🔍 Objects requiring manual review ({len(report.objects_needing_review)}):")
        for name in report.objects_needing_review:
            print(f"     • {name}")

    print(sep2)
    print()


# ══════════════════════════════════════════════════════════════════════════════
#  COMMAND: validate
# ══════════════════════════════════════════════════════════════════════════════

def cmd_validate(args: argparse.Namespace) -> int:
    """
    Validate already-converted Fabric T-SQL for residual Redshift syntax.
    """
    from app.validator.validator import validate_result
    from app.core.models import ConversionResult, ConversionStatus, ObjectType

    if args.file:
        sql_path = Path(args.file)
        if not sql_path.exists():
            print(f"[ERROR] File not found: {sql_path}", file=sys.stderr)
            return 2
        sql = sql_path.read_text(encoding="utf-8-sig")
        print(f"\n🔍  Validating : {sql_path.resolve()}")
    elif args.sql:
        sql = args.sql
        print(f"\n🔍  Validating  : inline SQL")
    else:
        print("[ERROR] Provide --file or --sql", file=sys.stderr)
        return 2

    dummy = ConversionResult(
        source_name="validation_target",
        target_name="validation_target",
        object_type=ObjectType.UNKNOWN,
        status=ConversionStatus.HIGH_CONFIDENCE,
        confidence_score=1.0,
        output_sql=sql,
    )
    result = validate_result(dummy)

    sep = "─" * 60
    print(sep)
    if not result.warnings:
        print("  ✅  No residual Redshift syntax detected. SQL looks clean!")
    else:
        print(f"  ⚠️   {len(result.warnings)} issue(s) found:\n")
        for w in result.warnings:
            print(f"  [{w.level.value}] {w.code}")
            print(f"    → {w.message}")
            if w.suggestion:
                print(f"    💡 {w.suggestion}")
            print()
    print(sep)

    return 0 if not result.warnings else 1


# ══════════════════════════════════════════════════════════════════════════════
#  COMMAND: server
# ══════════════════════════════════════════════════════════════════════════════

def cmd_server(args: argparse.Namespace) -> int:
    """Start the FastAPI REST API server via uvicorn."""
    try:
        import uvicorn
    except ImportError:
        print("[ERROR] uvicorn not installed. Run: pip install uvicorn[standard]", file=sys.stderr)
        return 2

    port = args.port or settings.api_port
    host = args.host or settings.api_host

    print(f"\n🚀  Starting Redshift → Fabric Converter")
    print(f"    Host    : {host}")
    print(f"    Port    : {port}")
    print(f"    ┌─────────────────────────────────────────────────┐")
    print(f"    │  🌐  UI       →  http://localhost:{port}/           │")
    print(f"    │  📖  API Docs →  http://localhost:{port}/docs        │")
    print(f"    │  ❤  Health  →  http://localhost:{port}/api/v1/health│")
    print(f"    └─────────────────────────────────────────────────┘\n")

    ensure_directories()

    uvicorn.run(
        "app.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=args.reload,
        log_level=settings.log_level.lower(),
    )
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  COMMAND: test
# ══════════════════════════════════════════════════════════════════════════════

def cmd_test(args: argparse.Namespace) -> int:
    """Run the test suite via pytest."""
    try:
        import pytest as _pytest
    except ImportError:
        print("[ERROR] pytest not installed. Run: pip install pytest pytest-asyncio", file=sys.stderr)
        return 2

    pytest_args: list[str] = ["-v"]

    suite = getattr(args, "suite", "all")
    if suite == "unit":
        pytest_args += ["tests/unit"]
    elif suite == "integration":
        pytest_args += ["tests/integration"]
    else:
        pytest_args += ["tests/"]

    if getattr(args, "cov", False):
        pytest_args += ["--cov=app", "--cov-report=term-missing", "--cov-report=html"]

    if getattr(args, "k", None):
        pytest_args += ["-k", args.k]

    print(f"\n🧪  Running tests  : {suite}")
    print(f"    pytest args   : {' '.join(pytest_args)}\n")

    exit_code = _pytest.main(pytest_args)
    return int(exit_code)


# ══════════════════════════════════════════════════════════════════════════════
#  COMMAND: demo
# ══════════════════════════════════════════════════════════════════════════════

def cmd_demo(_args: argparse.Namespace) -> int:
    """
    Run a built-in demo conversion using a handful of representative
    Redshift DDL statements — no files required.
    """
    from app.core.pipeline import convert_sql
    from app.reporter.reporter import build_report
    from app.output.generator import write_outputs

    DEMO_SQL = textwrap.dedent("""
        -- TABLE: student_login (with DISTKEY, SORTKEY, ENCODE, boolean)
        CREATE TABLE bi_alefdw.student_login (
            login_date_dw_id    bigint ENCODE raw,
            student_dw_id       bigint ENCODE az64,
            school_dw_id        bigint ENCODE raw DISTKEY,
            outside_school_flag boolean ENCODE raw,
            login_local_date_time timestamp without time zone ENCODE az64,
            login_date_time     timestamp without time zone ENCODE az64
        ) DISTSTYLE AUTO SORTKEY (school_dw_id, login_date_dw_id);

        -- TABLE: total_teachers (VARCHAR(65535), numeric precision, SORTKEY)
        CREATE TABLE bi_alefdw.total_teachers (
            local_date          date ENCODE az64,
            school_dw_id        bigint ENCODE az64,
            school_name         character varying(384) ENCODE lzo,
            school_latitude     numeric(10,6) ENCODE az64,
            school_longitude    numeric(10,6) ENCODE az64,
            school_label        character varying(65535) ENCODE lzo,
            week_number         numeric(18,0) ENCODE az64,
            holiday_flag        boolean ENCODE raw
        ) DISTSTYLE AUTO SORTKEY (local_date);

        -- TABLE: map_polygons (GEOMETRY — unsupported type, triggers warning)
        CREATE TABLE bi_alefdw.map_polygons (
            geometry    geometry ENCODE raw,
            gid_0       character varying(256) ENCODE lzo,
            name_0      character varying(256) ENCODE lzo
        ) DISTSTYLE AUTO;

        -- VIEW: with IS FALSE, DATE_TRUNC, NVL, ::date cast, schema placeholder
        CREATE OR REPLACE VIEW bi_alefdw.v_student_login_summary
        WITH NO SCHEMA BINDING AS
        SELECT
            sl.school_dw_id,
            NVL(sl.outside_school_flag, FALSE) IS FALSE AS inside_school_flag,
            DATE_TRUNC('week', sl.login_local_date_time)  AS login_week,
            DATE_TRUNC('month', sl.login_local_date_time) AS login_month,
            CURRENT_DATE                                  AS report_date,
            sl.login_date_time::date                      AS login_date,
            md5(sl.student_dw_id::varchar)                AS student_hash
        FROM bi_alefdw.student_login sl
        WHERE sl.outside_school_flag IS FALSE;

        -- MATERIALIZED VIEW → stored procedure pattern
        CREATE MATERIALIZED VIEW bi_alefdw.agg_login_daily_mv
        BACKUP NO DISTSTYLE AUTO AS
        SELECT
            school_dw_id,
            DATE_TRUNC('day', login_local_date_time) AS login_day,
            COUNT(*) AS login_count
        FROM bi_alefdw.student_login
        GROUP BY school_dw_id, DATE_TRUNC('day', login_local_date_time);
    """)

    print("\n" + "═" * 68)
    print("  DEMO: Redshift → Microsoft Fabric DDL Converter")
    print("═" * 68)
    print("  Converting 3 tables + 1 view + 1 materialized view …\n")

    t0 = time.perf_counter()
    batch = convert_sql(DEMO_SQL, source_filename="demo.sql")
    elapsed = (time.perf_counter() - t0) * 1000

    job_id = "demo"
    generated = write_outputs(batch, job_id=job_id)
    report = build_report(batch, job_id=job_id)

    _print_conversion_summary(batch, report, generated, elapsed)

    # Show a sample of generated SQL
    print("\n─── Sample output: first TABLE ─────────────────────────────────\n")
    if batch.table_results:
        print(batch.table_results[0].output_sql)

    print("\n─── Sample output: first VIEW ──────────────────────────────────\n")
    if batch.view_results:
        first_view = next((r for r in batch.view_results
                           if "PROCEDURE" not in r.output_sql), batch.view_results[0])
        print(first_view.output_sql)

    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  CLI argument parser
# ══════════════════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Redshift → Microsoft Fabric DDL Converter — CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              python run.py demo
              python run.py convert --file bi_alefdw_tables.sql
              python run.py convert --file bi_alefdw_tables.sql --out-dir ./results
              python run.py convert --sql "CREATE TABLE bi_alefdw.t (id bigint ENCODE az64) DISTSTYLE AUTO;"
              python run.py validate --file data/outputs/my_job/combined/all_converted.sql
              python run.py server
              python run.py server --port 9000 --reload
              python run.py test
              python run.py test --suite unit
              python run.py test --suite integration --cov
        """),
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # ── convert ──────────────────────────────────────────────────────────
    p_conv = sub.add_parser("convert", help="Convert Redshift DDL to Fabric T-SQL")
    src = p_conv.add_mutually_exclusive_group()
    src.add_argument("--file", "-f", metavar="PATH",
                     help="Path to a .sql file containing Redshift DDL")
    src.add_argument("--sql",  "-s", metavar="SQL",
                     help="Inline Redshift DDL SQL string")
    p_conv.add_argument("--out-dir", metavar="DIR",
                        help="Override output directory (default: data/outputs)")
    p_conv.add_argument("--tables-only", action="store_true",
                        help="Convert only CREATE TABLE statements")
    p_conv.add_argument("--views-only", action="store_true",
                        help="Convert only CREATE VIEW / MATERIALIZED VIEW statements")

    # ── validate ─────────────────────────────────────────────────────────
    p_val = sub.add_parser("validate", help="Validate converted T-SQL for residual Redshift syntax")
    src2 = p_val.add_mutually_exclusive_group()
    src2.add_argument("--file", "-f", metavar="PATH")
    src2.add_argument("--sql",  "-s", metavar="SQL")

    # ── server ───────────────────────────────────────────────────────────
    p_srv = sub.add_parser("server", help="Start the FastAPI REST API server")
    p_srv.add_argument("--host", default=None, help="Bind host (default: 0.0.0.0)")
    p_srv.add_argument("--port", type=int, default=None, help="Bind port (default: 8000)")
    p_srv.add_argument("--reload", action="store_true",
                       help="Enable auto-reload (development only)")

    # ── test ─────────────────────────────────────────────────────────────
    p_test = sub.add_parser("test", help="Run the test suite")
    p_test.add_argument("--suite", choices=["all", "unit", "integration"],
                        default="all", help="Which tests to run (default: all)")
    p_test.add_argument("--cov", action="store_true",
                        help="Enable coverage report")
    p_test.add_argument("-k", metavar="EXPRESSION",
                        help="pytest -k filter expression")

    # ── demo ─────────────────────────────────────────────────────────────
    sub.add_parser("demo", help="Run a built-in demo conversion (no files needed)")

    return parser


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    ensure_directories()

    dispatch = {
        "convert":  cmd_convert,
        "validate": cmd_validate,
        "server":   cmd_server,
        "test":     cmd_test,
        "demo":     cmd_demo,
    }

    handler = dispatch.get(args.command)
    if not handler:
        parser.print_help()
        return 1

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
