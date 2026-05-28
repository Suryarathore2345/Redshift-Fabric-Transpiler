-- Sample Redshift views for unit / integration testing
-- Derived from reference repo patterns

CREATE OR REPLACE VIEW bi_alefdw.v_student_login_summary
WITH NO SCHEMA BINDING
AS
SELECT
    sl.school_dw_id,
    sl.student_dw_id,
    sl.tenant_dw_id,
    NVL(sl.outside_school_flag, FALSE) IS FALSE AS inside_school_flag,
    DATE_TRUNC('week', sl.login_local_date_time) AS login_week,
    DATE_TRUNC('month', sl.login_local_date_time) AS login_month,
    CURRENT_DATE AS report_date,
    sl.login_date_time::date AS login_date,
    md5(sl.student_dw_id::varchar) AS student_hash
FROM bi_alefdw.student_login sl
WHERE sl.outside_school_flag IS FALSE;


CREATE OR REPLACE VIEW bi_alefdw.v_teacher_login_enriched
WITH NO SCHEMA BINDING
AS
WITH base AS (
    SELECT
        tl.school_dw_id,
        tl.teacher_dw_id,
        tl.login_local_date_time,
        date(tl.login_local_date_time) AS login_date,
        DATE_TRUNC('month', tl.login_local_date_time) AS login_month
    FROM bi_alefdw.teacher_login tl
),
monthly_agg AS (
    SELECT
        school_dw_id,
        teacher_dw_id,
        login_month,
        COUNT(*) AS login_count,
        MIN(login_date) AS first_login,
        MAX(login_date) AS last_login
    FROM base
    GROUP BY school_dw_id, teacher_dw_id, login_month
)
SELECT
    ma.*,
    LISTAGG(ma.login_month::varchar, ', ') WITHIN GROUP (ORDER BY ma.login_month)
        OVER (PARTITION BY ma.school_dw_id, ma.teacher_dw_id) AS all_months
FROM monthly_agg ma;


CREATE MATERIALIZED VIEW bi_alefdw.agg_student_login_mv
BACKUP NO
DISTSTYLE AUTO
AS
SELECT
    school_dw_id,
    student_dw_id,
    DATE_TRUNC('day', login_local_date_time) AS login_day,
    COUNT(*) AS login_count,
    MIN(login_local_date_time) AS first_login_ts,
    MAX(login_local_date_time) AS last_login_ts
FROM bi_alefdw.student_login
GROUP BY school_dw_id, student_dw_id, DATE_TRUNC('day', login_local_date_time);
