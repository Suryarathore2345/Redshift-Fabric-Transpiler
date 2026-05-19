-- ==============================================================================
-- Redshift → Microsoft Fabric DDL Conversion Output
-- Source:    demo.sql
-- Generated: 2026-05-19T05:25:00.954417+00:00
-- Objects:   5 total | 2 high-confidence | 3 partial | 0 manual review | 0 failed
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- TABLES
-- ------------------------------------------------------------------------------

-- [HIGH_CONFIDENCE] bi_alefdw.student_login
IF OBJECT_ID('${schema}.student_login', 'U') IS NULL
BEGIN
    CREATE TABLE ${schema}.student_login (
        student_dw_id BIGINT,
    school_dw_id BIGINT,
    outside_school_flag BIT,
    login_local_date_time DATETIME2(6),
    login_date_time DATETIME2(6)
    );
END;

-- [HIGH_CONFIDENCE] bi_alefdw.total_teachers
IF OBJECT_ID('${schema}.total_teachers', 'U') IS NULL
BEGIN
    CREATE TABLE ${schema}.total_teachers (
        [SORTKEY)
CREATE TABLE bi_alefdw.total_teachers (
    local_date] DATE,
    school_dw_id BIGINT,
    school_name VARCHAR(384),
    school_latitude DECIMAL(10,6),
    school_longitude DECIMAL(10,6),
    school_label VARCHAR(MAX),
    week_number DECIMAL(18,0),
    holiday_flag BIT
    );
END;

-- [PARTIAL] bi_alefdw.map_polygons
IF OBJECT_ID('${schema}.map_polygons', 'U') IS NULL
BEGIN
    CREATE TABLE ${schema}.map_polygons (
        [triggers warning)
CREATE TABLE bi_alefdw.map_polygons (] VARCHAR(MAX),
    gid_0 VARCHAR(256),
    name_0 VARCHAR(256)
    );
END;


-- ------------------------------------------------------------------------------
-- VIEWS / STORED PROCEDURES
-- ------------------------------------------------------------------------------

-- [PARTIAL] bi_alefdw.v_student_login_summary
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

-- [PARTIAL] bi_alefdw.agg_login_daily_mv
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
