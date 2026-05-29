-- ==============================================================================
-- Redshift → Microsoft Fabric DDL Conversion Output
-- Source:    inline_input.sql
-- Generated: 2026-05-29T04:55:38.028660+00:00
-- Objects:   1 total | 0 high-confidence | 1 partial | 0 manual review | 0 failed
-- ==============================================================================


-- ------------------------------------------------------------------------------
-- VIEWS / STORED PROCEDURES
-- ------------------------------------------------------------------------------

-- ══════════════════════════════════════════════════════════════════
-- MATERIALIZED VIEW -> PROCEDURE: bi_alefdw.v_student_summary
-- Target  : bi_alefdw.v_student_summary
-- Status  : ⚠️  PARTIAL  |  Confidence: 85%
-- Warnings: 3
--   ⚠ MATERIALIZED_VIEW: 'bi_alefdw.v_student_summary' is a MATERIALIZED VIEW. Fabric Warehouse
--      does not support materialised views natively.
--   💡 Convert to a stored procedure (usp_refresh_<name>) using the CTAS pattern: DROP TABLE IF
--      EXISTS + CREATE TABLE … AS SELECT …
--   ⚠ ORDINAL_GROUPBY_EXPANDED: Ordinal GROUP BY positions expanded to explicit column names (1
--      GROUP BY clause(s) processed). Fabric T-SQL does not support positional GROUP BY.
--   💡 Review expanded GROUP BY columns — CASE expressions are included verbatim (normalised to
--      single line). Aggregate columns (SUM/MAX/COUNT) are automatically excluded.
--   ⚠ MATVIEW_PATTERN: Materialised view 'v_student_summary' converted to stored procedure + CTAS
--      pattern.
--   💡 Schedule usp_refresh_{name} to run periodically via Fabric pipeline.
-- ══════════════════════════════════════════════════════════════════

CREATE OR ALTER PROCEDURE bi_alefdw.usp_refresh_v_student_summary
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRY

        -- Step 1: Drop stale staging table if it exists
        DROP TABLE IF EXISTS bi_alefdw.v_student_summary_staging;

        -- Step 2: CTAS - Build staging table with full transformation
        CREATE TABLE bi_alefdw.v_student_summary_staging
        AS
        SELECT
    sl.school_dw_id,
    sl.student_dw_id,
    ISNULL(sl.outside_school_flag, FALSE) = 0      AS inside_school_flag,
    DATETRUNC(iso_week, sl.login_local_date_time)     AS login_week,
    DATETRUNC(month, sl.login_local_date_time)    AS login_month,
    CONVERT(DATE, GETDATE())                                      AS report_date,
    CONVERT(DATE, sl.login_local_date_time)                   AS login_date,
    ISNULL(sl.student_dw_id, 0)                    AS student_id_safe,
    COUNT(*) AS login_count
FROM bi_alefdw.student_login sl
WHERE sl.outside_school_flag = 0
GROUP BY
    sl.school_dw_id,
    sl.student_dw_id,
    ISNULL(sl.outside_school_flag, FALSE) = 0,
    DATETRUNC(iso_week, sl.login_local_date_time),
    DATETRUNC(month, sl.login_local_date_time),
    CONVERT(DATE, GETDATE()),
    CONVERT(DATE, sl.login_local_date_time),
    ISNULL(sl.student_dw_id, 0);

        -- Step 3: Drop current live table and promote staging
        DROP TABLE IF EXISTS bi_alefdw.v_student_summary;

        EXEC sp_rename 'bi_alefdw.v_student_summary_staging', 'v_student_summary';

    END TRY
    BEGIN CATCH
        THROW;
    END CATCH;
END;
