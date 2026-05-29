-- ==============================================================================
-- Redshift → Microsoft Fabric DDL Conversion Output
-- Source:    inline_input.sql
-- Generated: 2026-05-29T05:29:55.342034+00:00
-- Objects:   1 total | 0 high-confidence | 1 partial | 0 manual review | 0 failed
-- ==============================================================================


-- ------------------------------------------------------------------------------
-- VIEWS / STORED PROCEDURES
-- ------------------------------------------------------------------------------

-- ══════════════════════════════════════════════════════════════════
-- MATERIALIZED LAKE VIEW: bi_alefdw.v_student_summary
-- Target  : ${os_bi_alefdw}.v_student_summary
-- Engine  : Fabric Lakehouse · Spark SQL (Delta Lake)
-- Status  : ⚠️  PARTIAL  |  Confidence: 95%
-- Warnings: 1
--   ⚠ ORDINAL_GROUPBY_EXPANDED: Ordinal GROUP BY positions expanded to explicit column names (1
--      GROUP BY clause(s)). Spark SQL does not support positional GROUP BY.
--   💡 Review expanded GROUP BY columns for correctness.
-- ══════════════════════════════════════════════════════════════════

CREATE OR REPLACE MATERIALIZED LAKE VIEW ${os_bi_alefdw}.v_student_summary
AS
SELECT
    sl.school_dw_id,
    sl.student_dw_id,
    COALESCE(sl.outside_school_flag, FALSE) = false      AS inside_school_flag,
    DATE_TRUNC('week', sl.login_local_date_time)     AS login_week,
    DATE_TRUNC('month', sl.login_local_date_time)    AS login_month,
    CURRENT_DATE                                      AS report_date,
    CAST(sl.login_local_date_time AS DATE)                   AS login_date,
    COALESCE(sl.student_dw_id, 0)                    AS student_id_safe,
    COUNT(*) AS login_count
FROM ${rs_bi_alefdw}.student_login sl
WHERE sl.outside_school_flag = false
GROUP BY
    sl.school_dw_id,
    sl.student_dw_id,
    COALESCE(sl.outside_school_flag, FALSE) = false,
    DATE_TRUNC('week', sl.login_local_date_time),
    DATE_TRUNC('month', sl.login_local_date_time),
    CURRENT_DATE,
    CAST(sl.login_local_date_time AS DATE),
    COALESCE(sl.student_dw_id, 0);
