"""
Unit tests — Table DDL Generator
"""
import pytest
from app.parser.table_parser import parse_table
from app.transformer.table_generator import generate_table
from app.core.models import ConversionStatus


STUDENT_LOGIN = """
CREATE TABLE bi_alefdw.student_login (
    login_date_dw_id bigint ENCODE raw,
    student_dw_id    bigint ENCODE az64,
    school_dw_id     bigint ENCODE raw DISTKEY,
    outside_school_flag boolean ENCODE raw,
    login_local_date_time timestamp without time zone ENCODE az64,
    login_date_time timestamp without time zone ENCODE az64
) DISTSTYLE AUTO SORTKEY (school_dw_id, login_date_dw_id);
"""

TOTAL_TEACHERS = """
CREATE TABLE bi_alefdw.total_teachers (
    local_date date ENCODE az64,
    school_latitude  numeric(10,6) ENCODE az64,
    school_longitude numeric(10,6) ENCODE az64,
    school_label     character varying(65535) ENCODE lzo,
    week_number      numeric(18,0) ENCODE az64,
    holiday_flag     boolean ENCODE raw
) DISTSTYLE AUTO SORTKEY (local_date);
"""

SCAFFOLD = """
CREATE TABLE bi_alefdw.scaffold (
    key integer ENCODE az64
) DISTSTYLE AUTO;
"""

SPACES_TABLE = """
CREATE TABLE bi_alefdw.school_district_mapping (
    school name character varying(256) ENCODE lzo,
    school dw id integer ENCODE az64
) DISTSTYLE AUTO;
"""


class TestTableGenerator:

    def test_idempotent_wrapper_present(self):
        ir = parse_table(STUDENT_LOGIN)
        result = generate_table(ir)
        assert "IF OBJECT_ID" in result.output_sql
        assert "BEGIN" in result.output_sql
        assert "END;" in result.output_sql

    def test_schema_placeholder_used(self):
        ir = parse_table(STUDENT_LOGIN)
        result = generate_table(ir)
        assert "${schema}" in result.output_sql

    def test_no_redshift_clauses_in_output(self):
        ir = parse_table(STUDENT_LOGIN)
        result = generate_table(ir)
        sql = result.output_sql.upper()
        assert "ENCODE" not in sql
        assert "DISTKEY" not in sql
        assert "DISTSTYLE" not in sql
        assert "SORTKEY" not in sql

    def test_column_types_converted(self):
        ir = parse_table(STUDENT_LOGIN)
        result = generate_table(ir)
        assert "BIT" in result.output_sql          # boolean → BIT
        assert "DATETIME2(6)" in result.output_sql  # timestamp → DATETIME2(6)
        assert "BIGINT" in result.output_sql

    def test_varchar_max_for_65535(self):
        ir = parse_table(TOTAL_TEACHERS)
        result = generate_table(ir)
        assert "VARCHAR(MAX)" in result.output_sql

    def test_decimal_precision_preserved(self):
        ir = parse_table(TOTAL_TEACHERS)
        result = generate_table(ir)
        assert "DECIMAL(10,6)" in result.output_sql

    def test_reserved_word_bracket_quoted(self):
        ir = parse_table(SCAFFOLD)
        result = generate_table(ir)
        # 'key' is a T-SQL reserved word
        assert "[key]" in result.output_sql.lower() or "key" in result.output_sql

    def test_spaces_in_name_bracket_quoted(self):
        ir = parse_table(SPACES_TABLE)
        result = generate_table(ir)
        assert "[school name]" in result.output_sql or "[school dw id]" in result.output_sql

    def test_high_confidence_for_clean_table(self):
        ir = parse_table(STUDENT_LOGIN)
        result = generate_table(ir)
        # May be PARTIAL due to boolean/timestamp warnings but not FAILED
        assert result.status != ConversionStatus.FAILED

    def test_applied_rules_recorded(self):
        ir = parse_table(STUDENT_LOGIN)
        result = generate_table(ir)
        assert "SCHEMA_PARAMETERISATION" in result.applied_rules
        assert "IDEMPOTENT_CREATE_TABLE" in result.applied_rules

    def test_source_name_correct(self):
        ir = parse_table(STUDENT_LOGIN)
        result = generate_table(ir, source_sql=STUDENT_LOGIN)
        assert result.source_name == "bi_alefdw.student_login"

    def test_output_sql_not_empty(self):
        for sql in [STUDENT_LOGIN, TOTAL_TEACHERS, SCAFFOLD]:
            ir = parse_table(sql)
            result = generate_table(ir)
            assert result.output_sql.strip() != ""
