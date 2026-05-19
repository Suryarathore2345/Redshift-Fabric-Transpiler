# Redshift → Fabric Conversion Report

**Job ID:** `20260519_052500_demo`  
**Source:** `demo.sql`  
**Generated:** 2026-05-19T05:25:00.956828+00:00  
**Duration:** 28 ms  

---

## Summary

| Metric | Value |
|--------|-------|
| Total Objects | 5 |
| ✅ High Confidence | 2 |
| ⚠️ Partial Conversion | 3 |
| 🔍 Manual Review Required | 0 |
| ❌ Failed | 0 |
| Success Rate | 40% |

---

## Tables (3)

| Table | Status | Confidence | Warnings |
|-------|--------|------------|----------|
| `bi_alefdw.student_login` | ✅ HIGH_CONFIDENCE | 100% | 0 |
| `bi_alefdw.total_teachers` | ✅ HIGH_CONFIDENCE | 95% | 1 |
| `bi_alefdw.map_polygons` | ⚠️ PARTIAL | 80% | 1 |

## Views / Stored Procedures (2)

| View | Status | Confidence | Warnings |
|------|--------|------------|----------|
| `bi_alefdw.v_student_login_summary` | ⚠️ PARTIAL | 95% | 1 |
| `bi_alefdw.agg_login_daily_mv` | ⚠️ PARTIAL | 90% | 2 |

---

## Warning Details

### `bi_alefdw.total_teachers`

- ⚠️ **RESIDUAL_SORTKEY**: [Validator] Residual SORTKEY clause found.
  - 💡 *SORTKEY has no Fabric equivalent; remove.*

### `bi_alefdw.map_polygons`

- ⚠️ **DATATYPE_GEOMETRY**: Redshift GEOMETRY type is unsupported in Microsoft Fabric Warehouse. Mapped to VARCHAR(MAX) as WKT representation. Spatial queries will not work.
  - 💡 *Redshift GEOMETRY type is unsupported in Microsoft Fabric Warehouse. Mapped to VARCHAR(MAX) as WKT representation. Spatial queries will not work.*

### `bi_alefdw.v_student_login_summary`

- ⚠️ **MD5_FUNCTION**: md5() is not a native Fabric Warehouse function. Retained as-is (reference repo pattern). Ensure a user-defined md5 scalar function exists in your Fabric environment, or replace with HASHBYTES('MD5', CAST(expr AS VARBINARY(MAX))).
  - 💡 *Create a SQL UDF or replace with HASHBYTES.*

### `bi_alefdw.agg_login_daily_mv`

- ⚠️ **MATERIALIZED_VIEW**: 'bi_alefdw.agg_login_daily_mv' is a MATERIALIZED VIEW. Fabric Warehouse does not support materialised views natively.
  - 💡 *Convert to a stored procedure (usp_refresh_<name>) using the CTAS pattern: DROP TABLE IF EXISTS + CREATE TABLE … AS SELECT …*
- ⚠️ **MATVIEW_PATTERN**: Materialised view 'agg_login_daily_mv' converted to stored procedure + CTAS pattern.
  - 💡 *Schedule usp_refresh_{name} to run periodically via Fabric pipeline.*

---

## Applied Transformation Rules

| Rule | Applied Count |
|------|---------------|
| `STRIP_DISTKEY_DISTSTYLE` | 3 |
| `IDEMPOTENT_CREATE_TABLE` | 3 |
| `SCHEMA_PARAMETERISATION` | 3 |
| `STRIP_SORTKEY` | 2 |
| `BRACKET_QUOTE_IDENTIFIER` | 2 |
| `CREATE_OR_ALTER_VIEW` | 2 |
| `MD5_WARNING` | 1 |
| `MATVIEW_TO_STORED_PROC` | 1 |
