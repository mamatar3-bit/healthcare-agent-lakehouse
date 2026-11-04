-- Operational tables written by the stored procedures.
-- ${catalog} is replaced by src/08_run_sql.py (or by you when running by hand).
-- Statements are separated by "-- @@" lines.

CREATE TABLE IF NOT EXISTS ${catalog}.ops.care_management_worklist (
  patient_id          STRING NOT NULL COMMENT 'Patient ID (PAT-nnnnnn)',
  eligibility_reasons STRING COMMENT 'Why the patient qualifies (Care Management Program Guide PH-150)',
  lace_score          INT    COMMENT 'LACE score for the most recent inpatient stay, if any',
  priority            STRING COMMENT 'high or medium',
  status              STRING COMMENT 'new, enrolled, declined, graduated or no_longer_eligible',
  first_flagged_date  DATE,
  as_of_date          DATE   COMMENT 'Date of the refresh that last evaluated this patient',
  updated_at          TIMESTAMP
) COMMENT 'Patients eligible for care management, maintained by ops.sp_refresh_care_management_worklist'
-- @@
CREATE TABLE IF NOT EXISTS ${catalog}.ops.appeal_queue (
  claim_id            STRING NOT NULL,
  patient_id          STRING,
  payer_id            STRING,
  payer_name          STRING,
  cpt_code            STRING,
  billed_amount       DECIMAL(12, 2),
  denial_code         STRING,
  denial_reason       STRING,
  appeal_deadline     DATE,
  days_to_deadline    INT,
  priority            INT    COMMENT '1 = deadline within 15 days, 2 = billed over $5,000, 3 = other (Policy RC-305)',
  recommended_action  STRING COMMENT 'First action for the denial code per Policy RC-305',
  status              STRING COMMENT 'open, in_progress, submitted, won, lost or expired',
  appeal_level        INT,
  created_at          TIMESTAMP,
  updated_at          TIMESTAMP
) COMMENT 'Denied claims to work, maintained by ops.sp_build_appeal_queue and ops.sp_log_appeal'
-- @@
CREATE TABLE IF NOT EXISTS ${catalog}.ops.appeal_events (
  event_id     BIGINT GENERATED ALWAYS AS IDENTITY,
  claim_id     STRING,
  event_type   STRING,
  appeal_level INT,
  notes        STRING,
  actor        STRING,
  event_ts     TIMESTAMP
) COMMENT 'Audit trail of appeal actions written by ops.sp_log_appeal'
-- @@
CREATE TABLE IF NOT EXISTS ${catalog}.ops.dq_results (
  run_id      STRING,
  check_name  STRING,
  failed_rows BIGINT,
  passed      BOOLEAN,
  checked_at  TIMESTAMP
) COMMENT 'Results of ops.sp_run_data_quality_checks'
