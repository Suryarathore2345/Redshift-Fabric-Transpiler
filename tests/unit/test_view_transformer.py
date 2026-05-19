"""
Unit tests — View Transformer
"""
import pytest
from app.parser.view_parser import parse_view
from app.transformer.view_transformer import (
    transform_view,
    _transform_schema_refs,
    _transform_date_trunc,
    _transform_boolean_is,
    _transform_nvl,
    _transform_listagg,
    _transform_current_date,
    _transform_cast_operator,
    _strip_table_name_suffixes,
    _transform_interval,
)
from app.core.models import ConversionStatus, ObjectType


SIMPLE_VIEW = """
CREATE OR REPLACE VIEW bi_alefdw.v_student_login
WITH NO SCHEMA BINDING
AS
SELECT
    school_dw_id,
    student_dw_id,
    login_local_date_time
FROM bi_alefdw.student_login
WHERE outside_school_flag IS FALSE;
"""

NVL_VIEW = """
CREATE OR REPLACE VIEW bi_alefdw.v_nvl_test
WITH NO SCHEMA BINDING
AS
SELECT NVL(school_dw_id, 0) AS school_id
FROM bi_alefdw.student_login;
"""

DATE_TRUNC_VIEW = """
CREATE OR REPLACE VIEW bi_alefdw.v_date_trunc_test
WITH NO SCHEMA BINDING
AS
SELECT
    DATE_TRUNC('week', login_local_date_time) AS login_week,
    DATE_TRUNC('month', login_local_date_time) AS login_month,
    DATE_TRUNC('day', login_local_date_time) AS login_day
FROM bi_alefdw.student_login;
"""

LISTAGG_VIEW = """
CREATE OR REPLACE VIEW bi_alefdw.v_listagg_test
WITH NO SCHEMA BINDING
AS
SELECT
    school_dw_id,
    LISTAGG(student_dw_id::varchar, ', ') WITHIN GROUP (ORDER BY student_dw_id) AS student_list
FROM bi_alefdw.student_login
GROUP BY school_dw_id;
"""

MATVIEW = """
CREATE MATERIALIZED VIEW bi_alefdw.agg_login_mv
BACKUP NO
AS
SELECT school_dw_id, COUNT(*) AS cnt
FROM bi_alefdw.student_login
GROUP BY school_dw_id;
"""

CTE_VIEW = """
CREATE OR REPLACE VIEW bi_alefdw.v_cte_test
WITH NO SCHEMA BINDING
AS
WITH base AS (
    SELECT school_dw_id, COUNT(*) AS cnt
    FROM bi_alefdw.student_login
    GROUP BY school_dw_id
)
SELECT * FROM base;
"""


class TestSchemaReplacement:

    def test_bi_alefdw_replaced(self):
        sql, _ = _transform_schema_refs("SELECT * FROM bi_alefdw.student_login")
        assert "bi_alefdw." not in sql
        assert "${rs_bi_alefdw}." in sql

    def test_multiple_refs_replaced(self):
        sql, _ = _transform_schema_refs(
            "SELECT * FROM bi_alefdw.t1 JOIN bi_alefdw.t2 ON t1.id = t2.id"
        )
        assert sql.count("${rs_bi_alefdw}.") == 2
        assert "bi_alefdw." not in sql


class TestTableSuffixStrip:

    def test_mv_suffix_stripped(self):
        sql, _ = _strip_table_name_suffixes(
            "SELECT * FROM ${rs_bi_alefdw}.students_lesson_progress_mv"
        )
        assert "_mv" not in sql
        assert "students_lesson_progress" in sql

    def test_view_suffix_stripped(self):
        sql, _ = _strip_table_name_suffixes(
            "SELECT * FROM ${rs_bi_alefdw}.school_summary_view"
        )
        assert "_view" not in sql


class TestBooleanTransform:

    def test_is_false_converted(self):
        sql, _ = _transform_boolean_is("WHERE flag IS FALSE")
        assert "= 0" in sql

    def test_is_true_converted(self):
        sql, _ = _transform_boolean_is("WHERE flag IS TRUE")
        assert "= 1" in sql

    def test_is_not_false_converted(self):
        sql, _ = _transform_boolean_is("WHERE flag IS NOT FALSE")
        assert "<> 0" in sql


class TestNvlTransform:

    def test_nvl_replaced(self):
        sql, _ = _transform_nvl("SELECT NVL(a, 0) FROM t")
        assert "ISNULL(" in sql
        assert "NVL(" not in sql

    def test_nvl_nested(self):
        sql, _ = _transform_nvl("SELECT NVL(NVL(a, b), 0) FROM t")
        assert "NVL(" not in sql
        assert sql.count("ISNULL(") == 2


class TestDateTrunc:

    def test_date_trunc_week_becomes_iso_week(self):
        sql, _ = _transform_date_trunc("DATE_TRUNC('week', login_ts)")
        assert "DATETRUNC(iso_week," in sql
        assert "DATE_TRUNC" not in sql

    def test_date_trunc_month(self):
        sql, _ = _transform_date_trunc("DATE_TRUNC('month', login_ts)")
        assert "DATETRUNC(month," in sql

    def test_date_trunc_day(self):
        sql, _ = _transform_date_trunc("DATE_TRUNC('day', login_ts)")
        assert "DATETRUNC(day," in sql


class TestCurrentDate:

    def test_current_date_replaced(self):
        sql, _ = _transform_current_date("SELECT CURRENT_DATE AS today")
        assert "CURRENT_DATE" not in sql
        assert "CONVERT(DATE, GETDATE())" in sql


class TestListagg:

    def test_listagg_to_string_agg(self):
        sql, _ = _transform_listagg(
            "LISTAGG(col, ', ') WITHIN GROUP (ORDER BY col)"
        )
        assert "STRING_AGG(" in sql
        assert "LISTAGG(" not in sql

    def test_listagg_distinct_flagged(self):
        _, warns = _transform_listagg("LISTAGG(DISTINCT col, ', ')")
        assert any("DISTINCT" in w.code for w in warns)


class TestIntervalTransform:

    def test_interval_add_approximated(self):
        sql, warns = _transform_interval("login_ts + INTERVAL '7 day'")
        assert "INTERVAL" not in sql or "DATEADD" in sql or "/*" in sql
        assert len(warns) > 0


class TestCastOperator:

    def test_cast_date(self):
        sql, _ = _transform_cast_operator("login_ts::date")
        assert "::" not in sql or "DATE" in sql

    def test_cast_int(self):
        sql, _ = _transform_cast_operator("some_col::int")
        assert "::" not in sql or "INT" in sql


class TestFullViewTransform:

    def test_no_schema_binding_stripped(self):
        ir = parse_view(SIMPLE_VIEW)
        result = transform_view(ir)
        assert "WITH NO SCHEMA BINDING" not in result.output_sql

    def test_create_or_alter_in_output(self):
        ir = parse_view(SIMPLE_VIEW)
        result = transform_view(ir)
        assert "CREATE OR ALTER VIEW" in result.output_sql

    def test_schema_placeholder_in_output(self):
        ir = parse_view(SIMPLE_VIEW)
        result = transform_view(ir)
        assert "${rs_bi_alefdw}" in result.output_sql

    def test_is_false_converted_in_view(self):
        ir = parse_view(SIMPLE_VIEW)
        result = transform_view(ir)
        assert "= 0" in result.output_sql

    def test_nvl_converted_in_view(self):
        ir = parse_view(NVL_VIEW)
        result = transform_view(ir)
        assert "ISNULL(" in result.output_sql
        assert "NVL(" not in result.output_sql

    def test_date_trunc_converted(self):
        ir = parse_view(DATE_TRUNC_VIEW)
        result = transform_view(ir)
        assert "DATETRUNC" in result.output_sql
        assert "DATE_TRUNC" not in result.output_sql
        assert "iso_week" in result.output_sql

    def test_listagg_converted(self):
        ir = parse_view(LISTAGG_VIEW)
        result = transform_view(ir)
        assert "STRING_AGG(" in result.output_sql

    def test_matview_becomes_stored_proc(self):
        ir = parse_view(MATVIEW)
        result = transform_view(ir)
        assert "CREATE OR ALTER PROCEDURE" in result.output_sql
        assert "usp_refresh_" in result.output_sql

    def test_cte_view_handled(self):
        ir = parse_view(CTE_VIEW)
        result = transform_view(ir)
        assert "WITH base AS" in result.output_sql or "base AS" in result.output_sql

    def test_status_not_failed(self):
        ir = parse_view(SIMPLE_VIEW)
        result = transform_view(ir)
        assert result.status != ConversionStatus.FAILED

    def test_applied_rules_populated(self):
        ir = parse_view(SIMPLE_VIEW)
        result = transform_view(ir)
        assert len(result.applied_rules) > 0
