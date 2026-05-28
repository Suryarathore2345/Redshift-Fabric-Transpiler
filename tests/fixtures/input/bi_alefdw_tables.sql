CREATE TABLE bi_alefdw.student_login ( login_date_dw_id bigint ENCODE raw, student_dw_id bigint ENCODE az64, tenant_dw_id bigint ENCODE az64, school_dw_id bigint ENCODE raw distkey, outside_school_flag boolean ENCODE raw, login_local_date_time timestamp without time zone ENCODE az64, login_date_time timestamp without time zone ENCODE az64 ) DISTSTYLE AUTO SORTKEY ( school_dw_id, login_date_dw_id );

CREATE TABLE bi_alefdw.teacher_login ( login_date_dw_id bigint ENCODE raw, teacher_dw_id bigint ENCODE az64, tenant_dw_id bigint ENCODE az64, school_dw_id bigint ENCODE raw, outside_school_flag boolean ENCODE raw, login_local_date_time timestamp without time zone ENCODE az64, login_date_time timestamp without time zone ENCODE az64 ) DISTSTYLE AUTO SORTKEY ( school_dw_id, login_date_dw_id );

CREATE TABLE bi_alefdw.tdc_mapping ( school_id integer ENCODE az64, school_name_en character varying(256) ENCODE lzo, school_name_ar character varying(256) ENCODE lzo, region character varying(256) ENCODE lzo, tdc_name character varying(256) ENCODE lzo, tdc_email character varying(256) ENCODE lzo, arm_name character varying(256) ENCODE lzo, arm_email character varying(256) ENCODE lzo ) DISTSTYLE AUTO;


CREATE TABLE bi_alefdw.student_adek_info ( student_adek_id character varying(50) ENCODE lzo, school_adek_id character varying(50) ENCODE lzo, grade character varying(50) ENCODE lzo, load_date date ENCODE az64 ) DISTSTYLE AUTO;


CREATE TABLE bi_alefdw.tableau_all_users ( all_user_email character varying(256) ENCODE lzo, user_status integer ENCODE az64, created_date date ENCODE az64 ) DISTSTYLE AUTO;


CREATE TABLE bi_alefdw.scaffold ( key integer ENCODE az64 ) DISTSTYLE AUTO;

CREATE TABLE bi_alefdw.total_teachers ( local_date date ENCODE az64, tenant_name character varying(765) ENCODE lzo, school_dw_id bigint ENCODE az64, school_name character varying(384) ENCODE lzo, adek_id character varying(256) ENCODE lzo, school_city_name character varying(100) ENCODE lzo, school_organisation character varying(250) ENCODE lzo, school_country_name character varying(100) ENCODE lzo, school_composition character varying(20) ENCODE lzo, school_latitude numeric(10,6) ENCODE az64, school_longitude numeric(10,6) ENCODE az64, school_label character varying(65535) ENCODE lzo, school_cx_cluster character varying(50) ENCODE lzo, school_created_time timestamp without time zone ENCODE az64, week_number numeric(18,0) ENCODE az64, week_year_number numeric(18,0) ENCODE az64, weekly_total_teachers bigint ENCODE az64, total_teachers bigint ENCODE az64, monthly_total_teachers bigint ENCODE az64, academic_year character varying(23) ENCODE lzo, school_id character varying(36) ENCODE lzo, org_dw_id bigint ENCODE az64, org_term integer ENCODE az64, term_start_date date ENCODE az64, term_end_date date ENCODE az64, holiday_flag boolean ENCODE raw, month_year_number integer ENCODE az64 ) DISTSTYLE AUTO SORTKEY ( local_date );


CREATE TABLE bi_alefdw.total_students ( local_date date ENCODE az64, tenant_name character varying(765) ENCODE lzo, school_dw_id bigint ENCODE az64 distkey, school_name character varying(384) ENCODE lzo, school_city_name character varying(100) ENCODE lzo, school_organisation character varying(250) ENCODE lzo, school_country_name character varying(100) ENCODE lzo, school_composition character varying(20) ENCODE lzo, school_latitude numeric(10,6) ENCODE az64, school_longitude numeric(10,6) ENCODE az64, adek_id character varying(256) ENCODE lzo, school_label character varying(65535) ENCODE lzo, school_created_time timestamp without time zone ENCODE az64, school_cx_cluster character varying(50) ENCODE lzo, academic_year character varying(49) ENCODE lzo, grade integer ENCODE az64, class character varying(1) ENCODE lzo, section_dw_id bigint ENCODE az64, section character varying(75) ENCODE lzo, student_tags character varying(256) ENCODE lzo, student_special_needs character varying(3) ENCODE lzo, week_number numeric(18,0) ENCODE az64, weekly_total_students bigint ENCODE az64, total_students bigint ENCODE az64, monthly_total_students bigint ENCODE az64, school_id character varying(36) ENCODE lzo, org_dw_id bigint ENCODE az64, org_term integer ENCODE az64, term_start_date date ENCODE az64, term_end_date date ENCODE az64, holiday_flag boolean ENCODE raw, month_year_number integer ENCODE az64, week_year_number integer ENCODE az64 ) DISTSTYLE AUTO SORTKEY ( local_date );


CREATE TABLE bi_alefdw.login_activity_reload_audit ( user_type character varying(36) ENCODE lzo, date_time_created timestamp without time zone ENCODE az64, num_records_loaded bigint ENCODE az64, from_date timestamp without time zone ENCODE az64, to_date timestamp without time zone ENCODE az64 ) DISTSTYLE AUTO;


CREATE TABLE bi_alefdw.login_activity_full_load_audit ( user_type character varying(36) ENCODE lzo, date_time_created timestamp without time zone ENCODE az64, num_records_loaded bigint ENCODE az64, from_date timestamp without time zone ENCODE az64, to_date timestamp without time zone ENCODE az64 ) DISTSTYLE AUTO;



CREATE TABLE bi_alefdw.adt_attempt1_percentile ( grade integer ENCODE az64, percentile integer ENCODE az64, attempt_1_min double precision ENCODE raw, attempt_1_max double precision ENCODE raw ) DISTSTYLE AUTO;



CREATE TABLE bi_alefdw.adt_attempt2_percentile ( grade integer ENCODE az64, percentile integer ENCODE az64, attempt_2_min double precision ENCODE raw, attempt_2_max double precision ENCODE raw ) DISTSTYLE AUTO;



CREATE TABLE bi_alefdw.adt_attempt3_percentile ( grade integer ENCODE az64, percentile integer ENCODE az64, attempt_3_min double precision ENCODE raw, attempt_3_max double precision ENCODE raw ) DISTSTYLE AUTO;



CREATE TABLE bi_alefdw.teacher_score_idn_school ( school_name character varying(256) ENCODE lzo, school_dw_id integer ENCODE az64 ) DISTSTYLE AUTO;



CREATE TABLE bi_alefdw.historical_data_reload_audit ( user_type character varying(36) ENCODE lzo, date_time_created timestamp without time zone ENCODE az64, num_records_loaded bigint ENCODE az64, from_date timestamp without time zone ENCODE az64, to_date timestamp without time zone ENCODE az64 ) DISTSTYLE AUTO;



CREATE TABLE bi_alefdw.nce_ese_lo_mastery_prev_year ( school_name character varying(384) ENCODE bytedict, fle_student_dw_id bigint ENCODE az64, academic_year character varying(49) ENCODE lzo, school_composition character varying(20) ENCODE bytedict, school_city_name character varying(100) ENCODE bytedict, subject character varying(255) ENCODE bytedict, lo_title character varying(750) ENCODE lzo, organisation_name character varying(50) ENCODE lzo, curr_grade_name integer ENCODE az64, fle_score bigint ENCODE az64 ) DISTSTYLE AUTO;



CREATE TABLE bi_alefdw.interim_checkpint_test_nce_ese_prev_year ( fle_student_dw_id bigint ENCODE az64, grade_k12grade integer ENCODE az64, class_gen_subject character varying(255) ENCODE lzo, school_name character varying(384) ENCODE lzo, school_composition character varying(20) ENCODE lzo, organisation_name character varying(50) ENCODE lzo, term_academic_period_order integer ENCODE az64, fle_score bigint ENCODE az64, academic_year character varying(49) ENCODE lzo, total_student bigint ENCODE az64 ) DISTSTYLE AUTO;



CREATE TABLE bi_alefdw.magg_cy_student_kpi_historical_data ( calendar_year_end_date date ENCODE az64, calendar_month_end_date date ENCODE az64, calendar_month_start_date date ENCODE az64, tenant_dw_id bigint ENCODE az64, tenant_name character varying(765) ENCODE lzo, content_repository_dw_id bigint ENCODE az64, content_repository_name character varying(50) ENCODE lzo, academic_year_start_date date ENCODE az64, academic_year_end_date date ENCODE az64, ay character varying(49) ENCODE lzo, registered_students bigint ENCODE az64, registered_schools bigint ENCODE az64, onboarded_students bigint ENCODE az64, onboarded_schools bigint ENCODE az64, students_logged_in bigint ENCODE az64, reg_school_cumsum bigint ENCODE az64, reg_student_cumsum bigint ENCODE az64, onb_school_cumsum bigint ENCODE az64, onb_student_cumsum bigint ENCODE az64, dense_rank bigint ENCODE az64, inserted_at timestamp without time zone ENCODE az64 ) DISTSTYLE AUTO;



CREATE TABLE bi_alefdw.magg_cy_teacher_kpi_historical_data ( calendar_year_end_date date ENCODE az64, calendar_month_end_date date ENCODE az64, calendar_month_start_date date ENCODE az64, academic_year_end_date date ENCODE az64, academic_year_start_date date ENCODE az64, tenant_dw_id bigint ENCODE az64, tenant_name character varying(765) ENCODE lzo, ay character varying(49) ENCODE lzo, content_repository_dw_id bigint ENCODE az64, content_repository_name character varying(50) ENCODE lzo, registered_teachers bigint ENCODE az64, onboarded_teachers bigint ENCODE az64, teachers_logged_in bigint ENCODE az64, max_reg_teachers bigint ENCODE az64, reg_cumsum bigint ENCODE az64, onb_teacher_cumsum bigint ENCODE az64, dense_rank bigint ENCODE az64, inserted_at timestamp without time zone ENCODE az64 ) DISTSTYLE AUTO;



CREATE TABLE bi_alefdw.yagg_cy_student_kpi_historical_data ( calendar_year_end_date date ENCODE az64, tenant_dw_id bigint ENCODE az64, tenant_name character varying(765) ENCODE lzo, content_repository_dw_id bigint ENCODE az64, content_repository_name character varying(50) ENCODE lzo, registered_students bigint ENCODE az64, registered_schools bigint ENCODE az64, onboarded_schools bigint ENCODE az64, onboarded_students bigint ENCODE az64, students_logged_in bigint ENCODE az64, inserted_at timestamp without time zone ENCODE az64 ) DISTSTYLE AUTO;




CREATE TABLE bi_alefdw.yagg_cy_teacher_kpi_historical_data ( calendar_year_end_date date ENCODE az64, tenant_dw_id bigint ENCODE az64, tenant_name character varying(765) ENCODE lzo, content_repository_dw_id bigint ENCODE az64 distkey, content_repository_name character varying(50) ENCODE lzo, registered_teachers bigint ENCODE az64, onboarded_teachers bigint ENCODE az64, teachers_logged_in bigint ENCODE az64, inserted_at timestamp without time zone ENCODE az64 ) DISTSTYLE AUTO;



CREATE TABLE bi_alefdw.student_login_military_historical_data ( academic_year character varying(23) ENCODE bytedict, academic_year_start_date date ENCODE az64, academic_year_end_date date ENCODE az64, school_dw_id bigint ENCODE az64 distkey, school_id character varying(36) ENCODE bytedict, school_name character varying(256) ENCODE bytedict, tenant_dw_id bigint ENCODE az64, reg_student_dw_id bigint ENCODE raw, reg_student_id character varying(36) ENCODE lzo, content_repository_dw_id bigint ENCODE az64, content_repository_name character varying(50) ENCODE lzo, local_date date ENCODE raw, tenant_name character varying(765) ENCODE lzo, grade_dw_id bigint ENCODE az64, grade_name character varying(250) ENCODE bytedict, log_student_dw_id bigint ENCODE az64, inserted_at timestamp with time zone ENCODE az64 ) DISTSTYLE KEY SORTKEY ( reg_student_dw_id, local_date );



CREATE TABLE bi_alefdw.students_stats_monthly ( calendar_year_end_date date ENCODE az64, calendar_month_end_date date ENCODE raw, calendar_month_start_date date ENCODE az64, tenant_dw_id bigint ENCODE az64, tenant_name character varying(765) ENCODE bytedict, content_repository_dw_id bigint ENCODE az64, content_repository_name character varying(50) ENCODE bytedict, school_city_name character varying(100) ENCODE bytedict, school_country_name character varying(100) ENCODE bytedict, academic_year_start_date date ENCODE az64, academic_year_end_date date ENCODE az64, ay character varying(49) ENCODE bytedict, student_dw_id bigint ENCODE az64, school_dw_id bigint ENCODE az64, school_name character varying(256) ENCODE lzo, grade_name character varying(250) ENCODE bytedict, first_login_date date ENCODE az64, is_active integer ENCODE az64, inserted_at timestamp without time zone ENCODE az64 ) DISTSTYLE AUTO SORTKEY ( calendar_month_end_date );



CREATE TABLE bi_alefdw.teacher_stats_monthly ( calendar_year_end_date date ENCODE az64, calendar_month_end_date date ENCODE az64, calendar_month_start_date date ENCODE az64, tenant_dw_id bigint ENCODE az64, tenant_name character varying(765) ENCODE bytedict, content_repository_dw_id bigint ENCODE az64, content_repository_name character varying(50) ENCODE bytedict, academic_year_start_date date ENCODE az64, academic_year_end_date date ENCODE az64, ay character varying(49) ENCODE bytedict, teacher_dw_id bigint ENCODE az64, school_dw_id bigint ENCODE az64, school_name character varying(256) ENCODE lzo, school_city_name character varying(100) ENCODE bytedict, school_country_name character varying(100) ENCODE bytedict, first_login_date date ENCODE az64, is_active integer ENCODE az64, inserted_at timestamp without time zone ENCODE az64 ) DISTSTYLE AUTO;



CREATE TABLE bi_alefdw.map_polygons ( geometry geometry ENCODE raw, gid_0 character varying(256) ENCODE lzo, name_0 character varying(256) ENCODE lzo, gid_1 character varying(256) ENCODE lzo, name_1 character varying(256) ENCODE lzo, nl_name_1 character varying(256) ENCODE lzo, gid_2 character varying(256) ENCODE lzo, name_2 character varying(256) ENCODE lzo, nl_name_2 character varying(256) ENCODE lzo, gid_3 character varying(256) ENCODE lzo, name_3 character varying(256) ENCODE lzo, varname_3 character varying(256) ENCODE lzo, nl_name_3 character varying(256) ENCODE lzo, type_3 character varying(256) ENCODE lzo, engtype_3 character varying(256) ENCODE lzo, cc_3 character varying(256) ENCODE lzo, hasc_3 character varying(256) ENCODE lzo, is_valid_polygon character varying(256) ENCODE lzo ) DISTSTYLE AUTO;



CREATE TABLE bi_alefdw.school_district_mapping ( school name character varying(256) ENCODE lzo, school dw id integer ENCODE az64, district character varying(256) ENCODE lzo, name 3 in shape file character varying(256) ENCODE lzo, district latitude double precision ENCODE raw, district longitude double precision ENCODE raw, school latitude double precision ENCODE raw, school longitude double precision ENCODE raw ) DISTSTYLE AUTO;



CREATE TABLE bi_alefdw.student_progress_core_military_historical_data ( class_dw_id bigint ENCODE az64, instructional_plan_id character varying(36) ENCODE lzo, school_dw_id bigint ENCODE az64, school_id character varying(36) ENCODE lzo, school_name character varying(576) ENCODE bytedict, class_title character varying(382) ENCODE lzo, class_gen_subject character varying(382) ENCODE bytedict, grade_name character varying(250) ENCODE bytedict, grade_dw_id bigint ENCODE az64, content_academic_year_id character varying(36) ENCODE lzo, content_academic_year_name character varying(20) ENCODE bytedict, student_dw_id bigint ENCODE raw, student_id character varying(36) ENCODE lzo, lo_dw_id bigint ENCODE az64, lo_title character varying(750) ENCODE lzo, week_start_date date ENCODE az64, week_end_date date ENCODE az64, term_academic_period_order integer ENCODE az64, term_start_date date ENCODE az64, term_end_date date ENCODE az64, pacing character varying(50) ENCODE lzo, lo_status character varying(11) ENCODE lzo, local_date date ENCODE raw, session_time double precision ENCODE raw, fle_session_time double precision ENCODE raw, fle_score numeric(14,4) ENCODE az64, academic_year character varying(23) ENCODE bytedict, academic_year_type character varying(3) ENCODE lzo, section_name character varying(256) ENCODE lzo, section_dw_id bigint ENCODE az64, section_id character varying(36) ENCODE lzo ) DISTSTYLE AUTO SORTKEY ( local_date, student_dw_id );



CREATE TABLE bi_alefdw.students_lesson_progress_military_historical_data ( local_date date ENCODE raw, fle_class_dw_id bigint ENCODE az64, lo_attempted bigint ENCODE az64, fle_lesson_category character varying(40) ENCODE lzo, fle_dw_id bigint ENCODE az64, fle_source character varying(10) ENCODE lzo, student_dw_id bigint ENCODE raw, student_id character varying(36) ENCODE lzo, school_dw_id bigint ENCODE az64 distkey, school_name character varying(576) ENCODE lzo, class_gen_subject character varying(255) ENCODE lzo, student_section_dw_id bigint ENCODE az64, fle_academic_year_dw_id bigint ENCODE az64, grade_k12grade integer ENCODE az64, session_time double precision ENCODE raw, fle_session_time double precision ENCODE raw, academic_year_start_date date ENCODE az64, academic_year_end_date date ENCODE az64, fle_score numeric(14,4) ENCODE az64, lo_status character varying(11) ENCODE lzo ) DISTSTYLE KEY SORTKEY ( local_date, student_dw_id );



CREATE TABLE bi_alefdw.student_login_backup_17oct25 ( login_date_dw_id bigint ENCODE az64, student_dw_id bigint ENCODE az64, tenant_dw_id bigint ENCODE az64, school_dw_id bigint ENCODE az64 distkey, outside_school_flag boolean ENCODE raw, login_local_date_time timestamp without time zone ENCODE az64, login_date_time timestamp without time zone ENCODE az64 ) DISTSTYLE AUTO;



