# Redshift → Fabric Conversion Report

**Job ID:** `20260529_045517_3c7bcf62`  
**Source:** `inline_input.sql`  
**Generated:** 2026-05-29T04:55:17.711629+00:00  
**Duration:** 2 ms  

---

## Summary

| Metric | Value |
|--------|-------|
| Total Objects | 1 |
| ✅ High Confidence | 0 |
| ⚠️ Partial Conversion | 0 |
| 🔍 Manual Review Required | 0 |
| ❌ Failed | 1 |
| Success Rate | 0% |

---

## Views / Stored Procedures (1)

| View | Status | Confidence | Warnings |
|------|--------|------------|----------|
| `error_0` | ❌ FAILED | 0% | 1 |

---

## Warning Details

### `error_0`

- ❌ **PARSE_ERROR**: Failed to convert statement: Cannot parse view header from: 'CREATE OR REPLACE MATERIALIZED VIEW bi_alefdw.v_student_summary\nWITH NO SCHEMA BINDING AS\nSELECT\n    sl.school_dw_id,\n  '
  - 💡 *Review original SQL and fix syntax issues before retrying.*
