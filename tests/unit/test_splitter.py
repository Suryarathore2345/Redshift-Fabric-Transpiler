"""
Unit tests — DDL Splitter
"""
import pytest
from app.parser.splitter import split_statements, classify_statement, classify_all
from app.core.models import ObjectType


class TestSplitStatements:

    def test_single_statement(self):
        sql = "CREATE TABLE foo.bar (id bigint);"
        result = split_statements(sql)
        assert len(result) == 1
        assert "CREATE TABLE" in result[0]

    def test_multiple_statements(self):
        sql = (
            "CREATE TABLE a.t1 (id bigint);\n"
            "CREATE TABLE a.t2 (id bigint);\n"
            "CREATE VIEW a.v1 AS SELECT 1;"
        )
        result = split_statements(sql)
        assert len(result) == 3

    def test_semicolon_inside_string_not_split(self):
        sql = "CREATE TABLE a.t1 (label character varying(100) DEFAULT 'a;b');"
        result = split_statements(sql)
        assert len(result) == 1

    def test_bom_stripped(self):
        sql = "\ufeffCREATE TABLE a.t1 (id bigint);"
        result = split_statements(sql)
        assert result[0].startswith("CREATE")

    def test_crlf_normalised(self):
        sql = "CREATE TABLE a.t1 (\r\n    id bigint\r\n);"
        result = split_statements(sql)
        assert len(result) == 1

    def test_comment_inside_statement(self):
        sql = (
            "CREATE TABLE a.t1 (\n"
            "    -- primary key\n"
            "    id bigint\n"
            ");"
        )
        result = split_statements(sql)
        assert len(result) == 1

    def test_block_comment(self):
        sql = "/* header */ CREATE TABLE a.t1 (id bigint);"
        result = split_statements(sql)
        assert len(result) == 1

    def test_empty_input(self):
        result = split_statements("")
        assert result == []

    def test_whitespace_only(self):
        result = split_statements("   \n  \n  ")
        assert result == []


class TestClassifyStatement:

    def test_classify_table(self):
        sql = "CREATE TABLE bi_alefdw.student_login (id bigint)"
        assert classify_statement(sql) == ObjectType.TABLE

    def test_classify_temp_table(self):
        sql = "CREATE TEMP TABLE staging.tmp (id bigint)"
        assert classify_statement(sql) == ObjectType.TABLE

    def test_classify_view(self):
        sql = "CREATE OR REPLACE VIEW bi_alefdw.v_foo AS SELECT 1"
        assert classify_statement(sql) == ObjectType.VIEW

    def test_classify_matview(self):
        sql = "CREATE MATERIALIZED VIEW bi_alefdw.agg_foo AS SELECT 1"
        assert classify_statement(sql) == ObjectType.MATERIALIZED_VIEW

    def test_classify_schema(self):
        sql = "CREATE SCHEMA bi_alefdw"
        assert classify_statement(sql) == ObjectType.SCHEMA

    def test_classify_unknown(self):
        sql = "INSERT INTO foo SELECT 1"
        assert classify_statement(sql) == ObjectType.UNKNOWN

    def test_classify_all_filters_unknown(self):
        stmts = [
            "CREATE TABLE a.t1 (id bigint)",
            "INSERT INTO a.t1 VALUES (1)",
            "CREATE VIEW a.v1 AS SELECT 1",
        ]
        classified = classify_all(stmts)
        assert len(classified) == 2
        types = {c.object_type for c in classified}
        assert ObjectType.TABLE in types
        assert ObjectType.VIEW in types
