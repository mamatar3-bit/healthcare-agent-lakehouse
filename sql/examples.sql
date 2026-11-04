-- Hands-on examples. Run in the SQL editor against your dev catalog.
USE CATALOG hc_dev;

-- Table-valued functions
SELECT * FROM gold.fn_denials_by_payer(DATE'2026-01-01', current_date()) ORDER BY denial_rate_pct DESC;
SELECT * FROM gold.fn_readmission_rate(add_months(current_date(), -6), current_date());
SELECT * FROM gold.fn_readmission_rate(DATE'2026-01-01', current_date(), 'Riverbend North');
SELECT * FROM gold.fn_patient_encounter_history('PAT-000042');

-- Scalar functions
SELECT gold.fn_lace_points(5, TRUE, 2, 1) AS lace;             -- 4 + 3 + 2 + 1 = 10
SELECT ai.lace_score(5, TRUE, 2, 1);                             -- same score from the Python UDF
SELECT gold.fn_mask_member_id(member_id), gold.fn_age_band(birth_date) FROM gold.dim_patient LIMIT 5;

-- Agent tools are just functions too
SELECT ai.get_claim_status((SELECT min(claim_id) FROM gold.fct_claims WHERE claim_status = 'denied'));
SELECT ai.check_prior_auth('72148', 'PAY-01');
SELECT ai.get_care_management_status((SELECT min(patient_id) FROM ops.care_management_worklist));

-- Stored procedures: OUT parameters need session variables
DECLARE OR REPLACE VARIABLE cm_active INT;
CALL ops.sp_refresh_care_management_worklist(current_date(), cm_active);
SELECT cm_active;
SELECT priority, count(*) FROM ops.care_management_worklist WHERE status = 'new' GROUP BY priority;

DECLARE OR REPLACE VARIABLE new_items INT;
DECLARE OR REPLACE VARIABLE expired_items INT;
CALL ops.sp_build_appeal_queue(new_items, expired_items);
SELECT new_items, expired_items;
SELECT * FROM ops.appeal_queue WHERE status = 'open' ORDER BY priority, days_to_deadline LIMIT 20;

-- Work an appeal (pick a claim from the queue above)
DECLARE OR REPLACE VARIABLE appeal_result STRING;
DECLARE OR REPLACE VARIABLE my_claim STRING DEFAULT (SELECT min(claim_id) FROM ops.appeal_queue WHERE status = 'open');
CALL ops.sp_log_appeal(my_claim, 'start', 'Requested clinical notes', appeal_result);
SELECT appeal_result;
SELECT * FROM ops.appeal_events ORDER BY event_ts DESC LIMIT 5;

-- Errors are raised with SIGNAL:
-- CALL ops.sp_log_appeal(my_claim, 'approve', NULL, appeal_result);   -- invalid action

DECLARE OR REPLACE VARIABLE dq_failed INT;
CALL ops.sp_run_data_quality_checks(FALSE, dq_failed);
SELECT * FROM ops.dq_results ORDER BY checked_at DESC LIMIT 6;
