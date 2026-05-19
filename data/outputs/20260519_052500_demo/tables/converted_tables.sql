-- ======================================================================
-- SECTION: TABLES
-- Generated: 2026-05-19T05:25:00.950442+00:00
-- ======================================================================

-- Object: bi_alefdw.student_login
-- Status: HIGH_CONFIDENCE  |  Confidence: 100%
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


-- Object: bi_alefdw.total_teachers
-- Status: HIGH_CONFIDENCE  |  Confidence: 95%
-- Warnings: 1
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


-- Object: bi_alefdw.map_polygons
-- Status: PARTIAL  |  Confidence: 80%
-- Warnings: 1
IF OBJECT_ID('${schema}.map_polygons', 'U') IS NULL
BEGIN
    CREATE TABLE ${schema}.map_polygons (
        [triggers warning)
CREATE TABLE bi_alefdw.map_polygons (] VARCHAR(MAX),
    gid_0 VARCHAR(256),
    name_0 VARCHAR(256)
    );
END;

