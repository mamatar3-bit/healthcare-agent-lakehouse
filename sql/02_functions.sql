-- SQL functions: scalar helpers, table-valued functions for analysts,
-- and two more agent tools in the ai schema.

-- ---------- scalar helpers -------------------------------------------------
CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_age_band(birth_date DATE)
RETURNS STRING
DETERMINISTIC
COMMENT 'Age band used across the network (0-17, 18-34, 35-49, 50-64, 65-79, 80+)'
RETURN CASE
  WHEN months_between(current_date(), birth_date) / 12 < 18 THEN '0-17'
  WHEN months_between(current_date(), birth_date) / 12 < 35 THEN '18-34'
  WHEN months_between(current_date(), birth_date) / 12 < 50 THEN '35-49'
  WHEN months_between(current_date(), birth_date) / 12 < 65 THEN '50-64'
  WHEN months_between(current_date(), birth_date) / 12 < 80 THEN '65-79'
  ELSE '80+'
END
-- @@
CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_mask_member_id(member_id STRING)
RETURNS STRING
DETERMINISTIC
COMMENT 'Masks a member ID to its last 4 characters, e.g. *****6789'
RETURN CASE WHEN member_id IS NULL THEN NULL ELSE concat('*****', right(member_id, 4)) END
-- @@
CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_lace_points(
  length_of_stay_days INT, acute_admission BOOLEAN, charlson_score INT, ed_visits_6m INT)
RETURNS INT
DETERMINISTIC
COMMENT 'LACE readmission index (0-19) in pure SQL; same logic as the Python UDF ai.lace_score'
RETURN
    CASE WHEN coalesce(length_of_stay_days, 0) < 1 THEN 0
         WHEN length_of_stay_days <= 3 THEN length_of_stay_days
         WHEN length_of_stay_days <= 6 THEN 4
         WHEN length_of_stay_days <= 13 THEN 5
         ELSE 7 END
  + CASE WHEN acute_admission THEN 3 ELSE 0 END
  + CASE WHEN coalesce(charlson_score, 0) <= 3 THEN coalesce(charlson_score, 0) ELSE 5 END
  + least(coalesce(ed_visits_6m, 0), 4)
-- @@
-- ---------- table-valued functions -----------------------------------------
CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_patient_encounter_history(target_patient_id STRING)
RETURNS TABLE (
  encounter_id STRING, encounter_type STRING, admit_date DATE, discharge_date DATE,
  length_of_stay_days INT, facility STRING, primary_dx_code STRING, primary_dx_description STRING,
  readmitted_within_30d BOOLEAN, total_billed DECIMAL(14, 2), total_paid DECIMAL(14, 2))
COMMENT 'All encounters for one patient, newest first, with billed and paid totals'
RETURN
  SELECT e.encounter_id, e.encounter_type, e.admit_date, e.discharge_date,
         e.length_of_stay_days, e.facility, e.primary_dx_code, e.primary_dx_description,
         e.readmitted_within_30d,
         CAST(sum(c.billed_amount) AS DECIMAL(14, 2)), CAST(sum(c.paid_amount) AS DECIMAL(14, 2))
  FROM ${catalog}.gold.fct_encounters e
  LEFT JOIN ${catalog}.gold.fct_claims c ON c.encounter_id = e.encounter_id
  WHERE e.patient_id = upper(trim(target_patient_id))
  GROUP BY ALL
  ORDER BY e.admit_date DESC
-- @@
CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_denials_by_payer(start_date DATE, end_date DATE)
RETURNS TABLE (
  payer_name STRING, claims BIGINT, denied_claims BIGINT, denial_rate_pct DOUBLE,
  denied_billed_usd DECIMAL(14, 2), prior_auth_denials BIGINT, top_denial_code STRING)
COMMENT 'Denial rate and dollars by payer for claims submitted between start_date and end_date (inclusive)'
RETURN
  SELECT payer_name,
         count(*),
         count_if(is_denied),
         round(100 * count_if(is_denied) / count(*), 1),
         CAST(coalesce(sum(CASE WHEN is_denied THEN billed_amount END), 0) AS DECIMAL(14, 2)),
         count_if(denial_code = 'CO-197'),
         mode(denial_code)
  FROM ${catalog}.gold.fct_claims
  WHERE submitted_date BETWEEN start_date AND end_date
  GROUP BY payer_name
-- @@
CREATE OR REPLACE FUNCTION ${catalog}.gold.fn_readmission_rate(
  start_month DATE, end_month DATE, target_facility STRING DEFAULT NULL)
RETURNS TABLE (facility STRING, index_admissions BIGINT, readmissions_30d BIGINT, readmission_rate_pct DOUBLE)
COMMENT '30-day readmission rate by facility between two discharge months (all facilities when target_facility is NULL)'
RETURN
  SELECT facility, sum(index_admissions), sum(readmissions_30d),
         round(100 * sum(readmissions_30d) / sum(index_admissions), 1)
  FROM ${catalog}.gold.agg_readmissions_monthly
  WHERE discharge_month BETWEEN date_trunc('month', start_month) AND date_trunc('month', end_month)
    AND (target_facility IS NULL OR facility = target_facility)
  GROUP BY facility
-- @@
-- ---------- agent tools (ai schema) ----------------------------------------
CREATE OR REPLACE FUNCTION ${catalog}.ai.get_care_management_status(
  target_patient_id STRING COMMENT 'Patient ID in the form PAT-000123')
RETURNS STRING
COMMENT 'Says whether ONE patient is on the care management worklist, why they qualify, their priority and status. Use for "is PAT-... eligible for / enrolled in care management?"'
RETURN SELECT coalesce(max(concat(
    'Patient ', patient_id, ' is on the care management worklist (status: ', status, ', priority: ', priority,
    ', first flagged ', CAST(first_flagged_date AS STRING), '). Reasons: ', eligibility_reasons, '.')),
  concat('Patient ', upper(trim(target_patient_id)), ' is not on the care management worklist as of the last refresh.'))
  FROM ${catalog}.ops.care_management_worklist
  WHERE patient_id = upper(trim(target_patient_id))
-- @@
CREATE OR REPLACE FUNCTION ${catalog}.ai.get_appeal_status(
  target_claim_id STRING COMMENT 'Claim ID in the form CLM-0001234')
RETURNS STRING
COMMENT 'Returns the appeal work-queue entry for ONE denied claim: priority, recommended action, deadline, appeal level and status, plus the latest appeal event.'
RETURN SELECT coalesce(max(concat(
    'Claim ', q.claim_id, ' appeal status: ', q.status, ' (level ', q.appeal_level, ', priority ', q.priority,
    '). Deadline ', CAST(q.appeal_deadline AS STRING), ' (', q.days_to_deadline, ' days left). Recommended action: ',
    q.recommended_action, coalesce(concat('. Last event: ', e.event_type, ' on ', CAST(e.event_ts AS STRING)), ''), '.')),
  concat('Claim ', upper(trim(target_claim_id)), ' is not in the appeal queue (it may be paid, pending, or past its deadline).'))
  FROM ${catalog}.ops.appeal_queue q
  LEFT JOIN (
    SELECT claim_id, event_type, event_ts,
           row_number() OVER (PARTITION BY claim_id ORDER BY event_ts DESC) AS rn
    FROM ${catalog}.ops.appeal_events
  ) e ON e.claim_id = q.claim_id AND e.rn = 1
  WHERE q.claim_id = upper(trim(target_claim_id))
