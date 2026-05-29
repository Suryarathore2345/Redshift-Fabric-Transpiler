# Redshift → Fabric Conversion Report

**Job ID:** `20260529_053016_31409a07`  
**Source:** `inline_input.sql`  
**Generated:** 2026-05-29T05:30:16.592972+00:00  
**Duration:** 11 ms  

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
| `bi_alefdw.v_student_summary` | ⚠️ PARTIAL | 95% | 1 |

---

## Warning Details

### `bi_alefdw.v_student_summary`

- ⚠️ **ORDINAL_GROUPBY_EXPANDED**: Ordinal GROUP BY positions expanded to explicit column names (1 GROUP BY clause(s)). Spark SQL does not support positional GROUP BY.
  - 💡 *Review expanded GROUP BY columns for correctness.*

---

## Applied Transformation Rules

| Rule | Applied Count |
|------|---------------|
| `SCHEMA_HARDCODED_MODE` | 1 |
| `ORDINAL_GROUPBY_EXPAND` | 1 |
| `CREATE_MATERIALIZED_LAKE_VIEW` | 1 |
| `INLINE_WARNING_COMMENTS` | 1 |
