-- ======================================================================
-- SECTION: VIEWS
-- Generated: 2026-05-19T05:25:00.952056+00:00
-- ======================================================================

-- Object: bi_alefdw.v_student_login_summary
-- Status: PARTIAL  |  Confidence: 95%
-- Warnings: 1
CREATE OR ALTER VIEW ${os_bi_alefdw}.v_student_login_summary AS
SELECT
    sl.school_dw_id,
    ISNULL(sl.outside_school_flag, FALSE) = 0 AS inside_school_flag,
    DATETRUNC(iso_week, sl.login_local_date_time)  AS login_week,
    DATETRUNC(month, sl.login_local_date_time) AS login_month,
    CONVERT(DATE, GETDATE())                                  AS report_date,
    sl.CONVERT(DATE, login_date_time)                      AS login_date,
    md5(sl.CAST(student_dw_id AS VARCHAR(MAX)))                AS student_hash
FROM ${rs_bi_alefdw}.student_login sl
WHERE sl.outside_school_flag = 0;


-- Object: bi_alefdw.agg_login_daily_mv
-- Status: PARTIAL  |  Confidence: 90%
-- Warnings: 2
CREATE OR ALTER PROCEDURE ${os_bi_alefdw}.usp_refresh_agg_login_daily_mv
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRY

        -- Step 1: Drop stale staging table if it exists
        DROP TABLE IF EXISTS ${os_bi_alefdw}.agg_login_daily_mv_staging;

        -- Step 2: CTAS - Build staging table with full transformation
        CREATE TABLE ${os_bi_alefdw}.agg_login_daily_mv_staging
        AS
        SELECT
    school_dw_id,
    DATETRUNC(day, login_local_date_time) AS login_day,
    COUNT(*) AS login_count
FROM ${rs_bi_alefdw}.student_login
GROUP BY school_dw_id, DATETRUNC(day, login_local_date_time);

        -- Step 3: Drop current live table and promote staging
        DROP TABLE IF EXISTS ${os_bi_alefdw}.agg_login_daily_mv;

        EXEC sp_rename '${os_bi_alefdw}.agg_login_daily_mv_staging', 'agg_login_daily_mv';

    END TRY
    BEGIN CATCH
        THROW;
    END CATCH;
END;

