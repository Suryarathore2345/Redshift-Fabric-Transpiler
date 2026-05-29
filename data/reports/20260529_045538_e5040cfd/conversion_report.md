# Redshift → Fabric Conversion Report

**Job ID:** `20260529_045538_e5040cfd`  
**Source:** `inline_input.sql`  
**Generated:** 2026-05-29T04:55:38.034269+00:00  
**Duration:** 32 ms  

---

## Summary

| Metric | Value |
|--------|-------|
| Total Objects | 1 |
| ✅ High Confidence | 0 |
| ⚠️ Partial Conversion | 1 |
| 🔍 Manual Review Required | 0 |
| ❌ Failed | 0 |
| Success Rate | 0% |

---

## Views / Stored Procedures (1)

| View | Status | Confidence | Warnings |
|------|--------|------------|----------|
| `bi_alefdw.v_student_summary` | ⚠️ PARTIAL | 80% | 4 |

---

## Warning Details

### `bi_alefdw.v_student_summary`

- ⚠️ **MATERIALIZED_VIEW**: 'bi_alefdw.v_student_summary' is a MATERIALIZED VIEW. Fabric Warehouse does not support materialised views natively.
  - 💡 *Convert to a stored procedure (usp_refresh_<name>) using the CTAS pattern: DROP TABLE IF EXISTS + CREATE TABLE … AS SELECT …*
- ⚠️ **ORDINAL_GROUPBY_EXPANDED**: Ordinal GROUP BY positions expanded to explicit column names (1 GROUP BY clause(s) processed). Fabric T-SQL does not support positional GROUP BY.
  - 💡 *Review expanded GROUP BY columns — CASE expressions are included verbatim (normalised to single line). Aggregate columns (SUM/MAX/COUNT) are automatically excluded.*
- ⚠️ **MATVIEW_PATTERN**: Materialised view 'v_student_summary' converted to stored procedure + CTAS pattern.
  - 💡 *Schedule usp_refresh_{name} to run periodically via Fabric pipeline.*
- ⚠️ **RESIDUAL_MATERIALIZED**: [Validator] MATERIALIZED VIEW keyword found in output.
  - 💡 *Convert to stored procedure + CTAS pattern.*

---

## Applied Transformation Rules

| Rule | Applied Count |
|------|---------------|
| `SCHEMA_HARDCODED_MODE` | 1 |
| `ORDINAL_GROUPBY_EXPAND` | 1 |
| `CREATE_OR_ALTER_VIEW` | 1 |
| `MATVIEW_TO_STORED_PROC` | 1 |
| `INLINE_WARNING_COMMENTS` | 1 |
