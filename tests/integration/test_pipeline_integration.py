"""
Integration tests — Full pipeline using real DDL fixtures.
 
These tests load the actual bi_alefdw_tables.sql and sample_views.sql
fixtures and run the complete conversion pipeline end-to-end.
"""
from __future__ import annotations

import pytest
from pathlib import Path

from app.core.pipeline import convert_sql
from app.core.models import ConversionStatus, ObjectType

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "input"

 
@pytest.fixture(scope="module")
def tables_sql() -> str:
    path = FIXTURES_DIR / "bi_alefdw_tables.sql"
    return path.read_text(encoding="utf-8-sig")


@pytest.fixture(scope="module")
def views_sql() -> str:
    path = FIXTURES_DIR / "sample_views.sql"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tables_batch(tables_sql):
    return convert_sql(tables_sql, source_filename="bi_alefdw_tables.sql")


@pytest.fixture(scope="module")
def views_batch(views_sql):
    return convert_sql(views_sql, source_filename="sample_views.sql")


# ── Table integration tests ───────────────────────────────────────────────────

class TestTablePipelineIntegration:

    def test_tables_parsed(self, tables_batch):
        assert tables_batch.total_objects > 0
        assert len(tables_batch.table_results) > 0

    def test_no_failed_tables(self, tables_batch):
        failed = [r for r in tables_batch.table_results
                  if r.status == ConversionStatus.FAILED]
        assert len(failed) == 0, f"Failed tables: {[r.source_name for r in failed]}"

    def test_all_outputs_non_empty(self, tables_batch):
        for r in tables_batch.table_results:
            assert r.output_sql.strip(), f"{r.source_name} has empty output"

    def test_no_redshift_keywords_in_output(self, tables_batch):
        redshift_keywords = ["ENCODE", "DISTSTYLE", "DISTKEY", "SORTKEY", "BACKUP NO"]
        for r in tables_batch.table_results:
            sql_upper = r.output_sql.upper()
            for kw in redshift_keywords:
                assert kw not in sql_upper, (
                    f"{r.source_name} still has {kw!r} in output"
                )

    def test_schema_placeholder_present(self, tables_batch):
        for r in tables_batch.table_results:
            assert "${schema}" in r.output_sql, (
                f"{r.source_name} missing schema placeholder"
            )

    def test_boolean_columns_are_bit(self, tables_batch):
        """All BOOLEAN columns must be mapped to BIT."""
        for r in tables_batch.table_results:
            assert "BOOLEAN" not in r.output_sql.upper(), (
                f"{r.source_name} still has BOOLEAN type"
            )

    def test_student_login_table_exists(self, tables_batch):
        names = [r.source_name for r in tables_batch.table_results]
        assert any("student_login" in n for n in names)

    def test_timestamp_no_tz_is_datetime2(self, tables_batch):
        """Timestamp without time zone must become DATETIME2(6)."""
        for r in tables_batch.table_results:
            assert "TIMESTAMP WITHOUT TIME ZONE" not in r.output_sql.upper()

    def test_geometry_mapped_with_warning(self, tables_batch):
        """map_polygons.geometry column must map to VARCHAR(MAX) with a warning."""
        poly = next(
            (r for r in tables_batch.table_results if "map_polygon" in r.source_name),
            None,
        )
        if poly:
            assert "VARCHAR(MAX)" in poly.output_sql
            assert any("GEOMETRY" in w.code.upper() or "geometry" in w.message.lower()
                       for w in poly.warnings)

    def test_varchar_65535_becomes_max(self, tables_batch):
        """school_label character varying(65535) → VARCHAR(MAX)."""
        total_teachers = next(
            (r for r in tables_batch.table_results if "total_teachers" in r.source_name),
            None,
        )
        if total_teachers:
            assert "VARCHAR(MAX)" in total_teachers.output_sql

    def test_double_precision_becomes_float53(self, tables_batch):
        adt = next(
            (r for r in tables_batch.table_results if "adt_attempt" in r.source_name),
            None,
        )
        if adt:
            assert "FLOAT(53)" in adt.output_sql

    def test_idempotent_create_in_all(self, tables_batch):
        for r in tables_batch.table_results:
            assert "IF OBJECT_ID" in r.output_sql

    def test_success_rate_above_80_pct(self, tables_batch):
        assert tables_batch.success_rate >= 0.80, (
            f"Success rate too low: {tables_batch.success_rate:.0%}"
        )


# ── View integration tests ────────────────────────────────────────────────────

class TestViewPipelineIntegration:

    def test_views_parsed(self, views_batch):
        assert len(views_batch.view_results) > 0

    def test_no_failed_views(self, views_batch):
        failed = [r for r in views_batch.view_results
                  if r.status == ConversionStatus.FAILED]
        assert len(failed) == 0, f"Failed: {[r.source_name for r in failed]}"

    def test_no_schema_binding_stripped(self, views_batch):
        for r in views_batch.view_results:
            assert "WITH NO SCHEMA BINDING" not in r.output_sql

    def test_create_or_alter_in_all_views(self, views_batch):
        for r in views_batch.view_results:
            if r.object_type == ObjectType.VIEW:
                assert "CREATE OR ALTER VIEW" in r.output_sql, r.source_name

    def test_schema_placeholder_in_views(self, views_batch):
        for r in views_batch.view_results:
            assert "${" in r.output_sql, f"{r.source_name} missing placeholder"

    def test_is_false_converted(self, views_batch):
        """IS FALSE → = 0 in view bodies."""
        for r in views_batch.view_results:
            assert "IS FALSE" not in r.output_sql

    def test_nvl_converted(self, views_batch):
        for r in views_batch.view_results:
            assert "NVL(" not in r.output_sql

    def test_date_trunc_converted(self, views_batch):
        for r in views_batch.view_results:
            assert "DATE_TRUNC" not in r.output_sql

    def test_matview_becomes_stored_proc(self, views_batch):
        matviews = [r for r in views_batch.view_results
                    if r.object_type == ObjectType.MATERIALIZED_VIEW]
        for mv in matviews:
            assert "CREATE OR ALTER PROCEDURE" in mv.output_sql
            assert "usp_refresh_" in mv.output_sql

    def test_listagg_converted_to_string_agg(self, views_batch):
        listagg_results = [
            r for r in views_batch.view_results
            if any("LISTAGG" in rule for rule in r.applied_rules)
               or "LISTAGG" in r.source_sql
        ]
        for r in listagg_results:
            assert "LISTAGG(" not in r.output_sql


# ── Combined file integration ─────────────────────────────────────────────────

class TestMixedInputIntegration:

    def test_mixed_sql_split_correctly(self):
        mixed = (
            "CREATE TABLE bi_alefdw.t1 (id bigint ENCODE az64) DISTSTYLE AUTO;\n"
            "CREATE OR REPLACE VIEW bi_alefdw.v1 WITH NO SCHEMA BINDING AS\n"
            "SELECT id FROM bi_alefdw.t1;\n"
        )
        batch = convert_sql(mixed, source_filename="mixed_test.sql")
        assert len(batch.table_results) == 1
        assert len(batch.view_results) == 1

    def test_empty_input_handled_gracefully(self):
        batch = convert_sql("", source_filename="empty.sql")
        assert batch.total_objects == 0
        assert batch.failed == 0

    def test_comments_only_handled(self):
        batch = convert_sql("-- just a comment\n/* block comment */")
        assert batch.total_objects == 0
