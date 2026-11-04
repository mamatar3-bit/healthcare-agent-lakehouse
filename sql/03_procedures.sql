-- Stored procedures (Databricks SQL / DBR 17+, Unity Catalog).
-- Each one encodes a rule from the policy documents so the data, the docs and the agent agree.

-- ---------------------------------------------------------------------------
-- Care Management Program Guide PH-150, "Eligibility":
--   LACE >= 10, or 2+ inpatient admissions in 12 months, or 3+ ED visits in 6 months,
--   or 2+ chronic conditions with HbA1c > 9% in the last 6 months.
CREATE OR REPLACE PROCEDURE ${catalog}.ops.sp_refresh_care_management_worklist(
  IN  as_of DATE COMMENT 'Evaluation date',
  OUT active_count INT COMMENT 'Patients with status new or enrolled after the refresh')
LANGUAGE SQL
SQL SECURITY INVOKER
MODIFIES SQL DATA
COMMENT 'Adds newly eligible patients to ops.care_management_worklist, refreshes reasons, and retires patients who no longer qualify'
AS BEGIN
  MERGE INTO ${catalog}.ops.care_management_worklist AS t
  USING (
    SELECT patient_id, lace,
           concat_ws('; ', filter(array(
             CASE WHEN lace >= 10 THEN concat('LACE ', lace, ' at last discharge') END,
             CASE WHEN inpatient_admits_12m >= 2 THEN concat(inpatient_admits_12m, ' inpatient admissions in 12 months') END,
             CASE WHEN ed_visits_6m >= 3 THEN concat(ed_visits_6m, ' ED visits in 6 months') END,
             CASE WHEN a1c_flag THEN concat(chronic_condition_count, ' chronic conditions with HbA1c ', last_a1c, '%') END
           ), x -> x IS NOT NULL)) AS reasons,
           CASE WHEN lace >= 10 OR inpatient_admits_12m >= 2 THEN 'high' ELSE 'medium' END AS priority
    FROM (
      SELECT f.*,
             CASE WHEN f.last_inpatient_los IS NOT NULL
                  THEN ${catalog}.gold.fn_lace_points(CAST(f.last_inpatient_los AS INT), TRUE,
                                                     CAST(f.charlson_score AS INT), CAST(f.ed_visits_6m AS INT)) END AS lace,
             coalesce(f.chronic_condition_count >= 2 AND f.last_a1c > 9
                      AND f.last_a1c_date >= add_months(as_of, -6), FALSE) AS a1c_flag
      FROM ${catalog}.gold.patient_risk_features f
    )
    WHERE lace >= 10 OR inpatient_admits_12m >= 2 OR ed_visits_6m >= 3 OR a1c_flag
  ) AS s
  ON t.patient_id = s.patient_id
  WHEN MATCHED AND t.status = 'no_longer_eligible' THEN UPDATE SET
    status = 'new', eligibility_reasons = s.reasons, lace_score = s.lace, priority = s.priority,
    as_of_date = as_of, updated_at = current_timestamp()
  WHEN MATCHED THEN UPDATE SET
    eligibility_reasons = s.reasons, lace_score = s.lace, priority = s.priority,
    as_of_date = as_of, updated_at = current_timestamp()
  WHEN NOT MATCHED THEN INSERT
    (patient_id, eligibility_reasons, lace_score, priority, status, first_flagged_date, as_of_date, updated_at)
    VALUES (s.patient_id, s.reasons, s.lace, s.priority, 'new', as_of, as_of, current_timestamp())
  WHEN NOT MATCHED BY SOURCE AND t.status = 'new' THEN UPDATE SET
    status = 'no_longer_eligible', as_of_date = as_of, updated_at = current_timestamp();

  SET active_count = (SELECT count(*) FROM ${catalog}.ops.care_management_worklist
                      WHERE status IN ('new', 'enrolled'));
END
-- @@
-- ---------------------------------------------------------------------------
-- Claims Denial Management and Appeals RC-305: queue priority and first action per denial code.
CREATE OR REPLACE PROCEDURE ${catalog}.ops.sp_build_appeal_queue(
  OUT new_items INT COMMENT 'Denied claims added to the queue by this run',
  OUT expired_items INT COMMENT 'Open items whose appeal deadline passed')
LANGUAGE SQL
SQL SECURITY INVOKER
MODIFIES SQL DATA
COMMENT 'Adds denied claims that can still be appealed to ops.appeal_queue, re-prioritizes open items and expires missed deadlines'
AS BEGIN
  DECLARE before_count INT DEFAULT (SELECT count(*) FROM ${catalog}.ops.appeal_queue);

  MERGE INTO ${catalog}.ops.appeal_queue AS t
  USING (
    SELECT c.claim_id, c.patient_id, c.payer_id, c.payer_name, c.cpt_code, c.billed_amount,
           c.denial_code, c.denial_reason, c.appeal_deadline,
           datediff(c.appeal_deadline, current_date()) AS days_to_deadline,
           CASE WHEN datediff(c.appeal_deadline, current_date()) <= 15 THEN 1
                WHEN c.billed_amount > 5000 THEN 2 ELSE 3 END AS priority,
           CASE c.denial_code
             WHEN 'CO-197' THEN 'Check for an existing authorization and submit a corrected claim; if none and the service was urgent, request retro-authorization'
             WHEN 'CO-16'  THEN 'Correct the missing information and resubmit (not an appeal)'
             WHEN 'CO-50'  THEN 'Request clinical notes and file a first-level appeal with a letter of medical necessity'
             WHEN 'CO-29'  THEN 'Appeal only with proof of timely original submission'
             WHEN 'CO-22'  THEN 'Verify primary and secondary coverage and rebill the correct payer'
             ELSE 'Review the remittance advice and route to a denial specialist'
           END AS recommended_action
    FROM ${catalog}.gold.fct_claims c
    WHERE c.claim_status = 'denied' AND c.appeal_deadline >= current_date()
  ) AS s
  ON t.claim_id = s.claim_id
  WHEN MATCHED AND t.status IN ('open', 'in_progress') THEN UPDATE SET
    days_to_deadline = s.days_to_deadline, priority = s.priority, updated_at = current_timestamp()
  WHEN NOT MATCHED THEN INSERT
    (claim_id, patient_id, payer_id, payer_name, cpt_code, billed_amount, denial_code, denial_reason,
     appeal_deadline, days_to_deadline, priority, recommended_action, status, appeal_level, created_at, updated_at)
    VALUES (s.claim_id, s.patient_id, s.payer_id, s.payer_name, s.cpt_code, s.billed_amount, s.denial_code,
            s.denial_reason, s.appeal_deadline, s.days_to_deadline, s.priority, s.recommended_action,
            'open', 1, current_timestamp(), current_timestamp());

  SET new_items = (SELECT count(*) FROM ${catalog}.ops.appeal_queue) - before_count;

  SET expired_items = (SELECT count(*) FROM ${catalog}.ops.appeal_queue
                       WHERE status IN ('open', 'in_progress') AND appeal_deadline < current_date());
  UPDATE ${catalog}.ops.appeal_queue
     SET status = 'expired', updated_at = current_timestamp()
   WHERE status IN ('open', 'in_progress') AND appeal_deadline < current_date();
END
-- @@
-- ---------------------------------------------------------------------------
-- Work an appeal: validates the claim, the action and the deadline, updates the queue,
-- and writes an audit event. Errors are raised with SIGNAL so callers see why.
CREATE OR REPLACE PROCEDURE ${catalog}.ops.sp_log_appeal(
  IN  p_claim_id STRING COMMENT 'Claim ID, e.g. CLM-0001234',
  IN  p_action   STRING COMMENT 'start, submit, escalate, won or lost',
  IN  p_notes    STRING COMMENT 'Free-text note for the audit trail',
  OUT result     STRING COMMENT 'Summary of what changed')
LANGUAGE SQL
SQL SECURITY INVOKER
MODIFIES SQL DATA
COMMENT 'Records an appeal action for a denied claim in ops.appeal_queue and ops.appeal_events'
AS BEGIN
  DECLARE v_claim    STRING DEFAULT upper(trim(p_claim_id));
  DECLARE v_action   STRING DEFAULT lower(trim(p_action));
  DECLARE v_status   STRING;
  DECLARE v_deadline DATE;
  DECLARE v_level    INT;
  DECLARE v_billed   DECIMAL(12, 2);
  DECLARE v_new_status STRING;

  SET v_status   = (SELECT max(status) FROM ${catalog}.ops.appeal_queue WHERE claim_id = v_claim);
  SET v_deadline = (SELECT max(appeal_deadline) FROM ${catalog}.ops.appeal_queue WHERE claim_id = v_claim);
  SET v_level    = (SELECT max(appeal_level) FROM ${catalog}.ops.appeal_queue WHERE claim_id = v_claim);
  SET v_billed   = (SELECT max(billed_amount) FROM ${catalog}.ops.appeal_queue WHERE claim_id = v_claim);

  IF v_status IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Claim is not in the appeal queue. Run ops.sp_build_appeal_queue first.';
  ELSEIF v_action NOT IN ('start', 'submit', 'escalate', 'won', 'lost') THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'p_action must be one of: start, submit, escalate, won, lost.';
  ELSEIF v_status IN ('won', 'lost', 'expired') THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'This appeal is closed (won, lost or expired).';
  ELSEIF v_action IN ('start', 'submit', 'escalate') AND v_deadline < current_date() THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'The appeal deadline has passed.';
  ELSEIF v_action = 'escalate' AND v_billed <= 2500 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Second-level appeals are only for amounts over $2,500 (Policy RC-305).';
  END IF;

  SET v_new_status = CASE v_action
                       WHEN 'start' THEN 'in_progress'
                       WHEN 'submit' THEN 'submitted'
                       WHEN 'escalate' THEN 'in_progress'
                       ELSE v_action END;

  UPDATE ${catalog}.ops.appeal_queue
     SET status = v_new_status,
         appeal_level = CASE WHEN v_action = 'escalate' THEN v_level + 1 ELSE v_level END,
         updated_at = current_timestamp()
   WHERE claim_id = v_claim;

  INSERT INTO ${catalog}.ops.appeal_events (claim_id, event_type, appeal_level, notes, actor, event_ts)
  VALUES (v_claim, v_action, CASE WHEN v_action = 'escalate' THEN v_level + 1 ELSE v_level END,
          p_notes, session_user(), current_timestamp());

  SET result = concat(v_claim, ': ', v_status, ' -> ', v_new_status,
                      CASE WHEN v_action = 'escalate' THEN concat(' (level ', v_level + 1, ')') ELSE '' END);
END
-- @@
-- ---------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE ${catalog}.ops.sp_run_data_quality_checks(
  IN  fail_on_error BOOLEAN COMMENT 'Raise an error when any check fails',
  OUT failed_checks INT COMMENT 'Number of failed checks in this run')
LANGUAGE SQL
SQL SECURITY INVOKER
MODIFIES SQL DATA
COMMENT 'Runs business-rule data quality checks on the gold layer and logs them to ops.dq_results'
AS BEGIN
  DECLARE v_run STRING DEFAULT uuid();

  INSERT INTO ${catalog}.ops.dq_results
  SELECT v_run, check_name, failed_rows, failed_rows = 0, current_timestamp()
  FROM (
    SELECT 'claims_paid_above_billed' AS check_name, count(*) AS failed_rows
      FROM ${catalog}.gold.fct_claims WHERE paid_amount > billed_amount
    UNION ALL
    SELECT 'denied_claims_without_reason', count(*)
      FROM ${catalog}.gold.fct_claims WHERE is_denied AND denial_reason IS NULL
    UNION ALL
    SELECT 'encounters_without_patient', count(*)
      FROM ${catalog}.gold.fct_encounters e
      LEFT ANTI JOIN ${catalog}.gold.dim_patient_deid p ON p.patient_id = e.patient_id
    UNION ALL
    SELECT 'negative_length_of_stay', count(*)
      FROM ${catalog}.gold.fct_encounters WHERE length_of_stay_days < 0
    UNION ALL
    SELECT 'readmission_rate_out_of_range', count(*)
      FROM ${catalog}.gold.agg_readmissions_monthly WHERE readmission_rate_30d NOT BETWEEN 0 AND 1
    UNION ALL
    SELECT 'bronze_encounters_stale_36h',
           CASE WHEN max(_loaded_at) < current_timestamp() - INTERVAL 36 HOURS THEN 1 ELSE 0 END
      FROM ${catalog}.bronze.encounters
  ) AS checks;

  SET failed_checks = (SELECT count(*) FROM ${catalog}.ops.dq_results WHERE run_id = v_run AND NOT passed);

  IF fail_on_error AND failed_checks > 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Data quality checks failed. See ops.dq_results for this run.';
  END IF;
END
