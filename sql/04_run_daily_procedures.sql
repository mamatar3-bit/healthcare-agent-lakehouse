-- Daily operational run, as one SQL script (a compound statement) so OUT variables
-- stay in scope. Returns a one-row summary.
BEGIN
  DECLARE dq_failed INT;
  DECLARE cm_active INT;
  DECLARE appeals_new INT;
  DECLARE appeals_expired INT;

  CALL ${catalog}.ops.sp_run_data_quality_checks(TRUE, dq_failed);
  CALL ${catalog}.ops.sp_refresh_care_management_worklist(current_date(), cm_active);
  CALL ${catalog}.ops.sp_build_appeal_queue(appeals_new, appeals_expired);

  SELECT dq_failed       AS dq_failed_checks,
         cm_active       AS care_management_active,
         appeals_new     AS appeals_added,
         appeals_expired AS appeals_expired;
END
