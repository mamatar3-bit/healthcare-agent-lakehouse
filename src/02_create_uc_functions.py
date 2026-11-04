# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · Unity Catalog functions (agent tools)
# MAGIC Deterministic, governed lookups the agent calls with exact identifiers. Each function's `COMMENT`
# MAGIC is what the LLM reads to decide how to call it, so keep them precise.
# MAGIC
# MAGIC | function | answers |
# MAGIC |---|---|
# MAGIC | `get_patient_summary(patient_id)` | minimum-necessary snapshot of one patient (no name/DOB) |
# MAGIC | `get_claim_status(claim_id)` | status, amounts, denial reason, appeal deadline |
# MAGIC | `check_prior_auth(cpt_code, payer_id)` | whether a payer requires PA for a procedure |
# MAGIC | `get_formulary_status(drug_name, payer_id)` | tier, PA and step therapy for a drug |
# MAGIC | `check_drug_interaction(drug_1, drug_2)` | known interaction from the demo reference table |
# MAGIC | `lace_score(...)` | Python UDF: LACE index from its four inputs |
# MAGIC | `calculate_readmission_risk(patient_id)` | LACE score for a patient from gold features |

# COMMAND ----------

dbutils.widgets.text("catalog", "hc_dev")
catalog = dbutils.widgets.get("catalog")
ai, gold, ref = f"{catalog}.ai", f"{catalog}.gold", f"{catalog}.ref"

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE FUNCTION {ai}.get_patient_summary(
  target_patient_id STRING COMMENT 'Patient ID in the form PAT-000123'
)
RETURNS STRING
COMMENT 'Returns a minimum-necessary clinical and utilization summary for ONE patient: age band, sex, payer, chronic conditions, Charlson score, ED visits in 6 months, inpatient admissions in 12 months, last discharge, last HbA1c, active medications and open denied claims. Contains no name, birth date or address. Use when the user gives a specific patient ID.'
RETURN SELECT COALESCE(MAX(CONCAT(
    'Patient ', patient_id, ': ', age_band, ' ', sex, ', payer ', payer_id, ' (', payer_type, ')',
    '. Chronic conditions: ', COALESCE(chronic_conditions, 'none recorded'),
    '. Charlson score: ', charlson_score,
    '. ED visits (6m): ', ed_visits_6m, '. Inpatient admissions (12m): ', inpatient_admits_12m,
    '. Last discharge: ', COALESCE(CAST(last_discharge_date AS STRING), 'none'),
    COALESCE(CONCAT(' (LOS ', last_inpatient_los, ' days, to ', last_discharge_disposition, ')'), ''),
    '. Last HbA1c: ', COALESCE(CONCAT(last_a1c, '% on ', CAST(last_a1c_date AS STRING)), 'none'),
    '. Active medications: ', COALESCE(active_medications, 'none'),
    '. Open denied claims: ', open_denied_claims, '.'
  )), CONCAT('No patient found with ID ', target_patient_id))
  FROM {gold}.patient_risk_features
  WHERE patient_id = upper(trim(target_patient_id))
""")

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE FUNCTION {ai}.get_claim_status(
  target_claim_id STRING COMMENT 'Claim ID in the form CLM-0001234'
)
RETURNS STRING
COMMENT 'Returns the status of ONE claim: payer, CPT code, billed/allowed/paid amounts, status, denial reason, days to adjudicate and the appeal deadline for denied claims. Use when the user gives a specific claim ID.'
RETURN SELECT COALESCE(MAX(CONCAT(
    'Claim ', claim_id, ' for patient ', patient_id, ' (', payer_name, '): CPT ', cpt_code, ' ', cpt_description,
    '. Status: ', upper(claim_status),
    '. Billed $', billed_amount, ', allowed $', allowed_amount, ', paid $', paid_amount,
    '. Submitted ', CAST(submitted_date AS STRING),
    COALESCE(CONCAT(', adjudicated ', CAST(adjudicated_date AS STRING), ' (', days_to_adjudicate, ' days)'), ', not yet adjudicated'),
    COALESCE(CONCAT('. Denial: ', denial_reason), ''),
    COALESCE(CONCAT('. Appeal deadline: ', CAST(appeal_deadline AS STRING)), ''),
    CASE WHEN prior_auth_required THEN '. This payer requires prior authorization for this CPT code.' ELSE '.' END
  )), CONCAT('No claim found with ID ', target_claim_id))
  FROM {gold}.fct_claims
  WHERE claim_id = upper(trim(target_claim_id))
""")

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE FUNCTION {ai}.check_prior_auth(
  target_cpt_code STRING COMMENT 'Five-digit CPT procedure code, e.g. 72148',
  target_payer_id STRING COMMENT 'Payer ID PAY-01..PAY-05 (PAY-01 Summit PPO, PAY-02 Summit HMO, PAY-03 Bluepoint Medicare Advantage, PAY-04 Commonwealth Medicaid, PAY-05 Self-pay)'
)
RETURNS STRING
COMMENT 'Says whether a payer requires prior authorization for a CPT code, with turnaround times and documentation notes. Use for questions like "does PAY-01 need PA for an MRI of the lumbar spine (72148)?"'
RETURN SELECT CASE
    WHEN MAX(r.payer_id) IS NULL THEN CONCAT('No prior authorization rule on file for CPT ', target_cpt_code, ' with payer ', target_payer_id,
         ': PA is not required per the reference table. Verify eligibility and benefits before scheduling.')
    WHEN MAX(CAST(r.requires_prior_auth AS INT)) = 1 THEN CONCAT('Prior authorization IS required for CPT ', target_cpt_code, ' (', MAX(c.cpt_description), ') with ', MAX(p.payer_name),
         '. Standard decision in ', MAX(r.standard_turnaround_days), ' days; urgent in ', MAX(r.urgent_turnaround_hours), ' hours. Notes: ', MAX(r.notes))
    ELSE CONCAT('Prior authorization is NOT required for CPT ', target_cpt_code, ' with ', MAX(p.payer_name), '. Notes: ', MAX(r.notes))
  END
  FROM (SELECT 1) dummy
  LEFT JOIN {ref}.prior_auth_rules r ON r.cpt_code = trim(target_cpt_code) AND r.payer_id = upper(trim(target_payer_id))
  LEFT JOIN {ref}.cpt_codes c ON c.cpt_code = r.cpt_code
  LEFT JOIN {ref}.payers p ON p.payer_id = r.payer_id
""")

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE FUNCTION {ai}.get_formulary_status(
  drug STRING COMMENT 'Generic drug name, e.g. semaglutide',
  target_payer_id STRING COMMENT 'Payer ID PAY-01..PAY-05'
)
RETURNS STRING
COMMENT 'Returns formulary coverage for a drug under a payer: tier (1 = preferred generic ... 5 = specialty), prior authorization, step therapy and quantity limits. Use for drug coverage questions with a named drug and payer.'
RETURN SELECT COALESCE(MAX(CONCAT(
    initcap(drug_name), ' on ', payer_id, ': tier ', tier,
    CASE WHEN requires_prior_auth THEN ', prior authorization required' ELSE ', no prior authorization' END,
    COALESCE(CONCAT(', step therapy: ', step_therapy), ''),
    COALESCE(CONCAT(', quantity limit: ', quantity_limit), ''), '.'
  )), CONCAT(initcap(drug), ' is not on the formulary for ', target_payer_id, '. A formulary exception request may be needed.'))
  FROM {ref}.formulary
  WHERE drug_name = lower(trim(drug)) AND payer_id = upper(trim(target_payer_id))
""")

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE FUNCTION {ai}.check_drug_interaction(
  drug_1 STRING COMMENT 'First generic drug name',
  drug_2 STRING COMMENT 'Second generic drug name'
)
RETURNS STRING
COMMENT 'Looks up a known interaction between two drugs in the demo reference table (not exhaustive, not clinical decision support). Returns severity, effect and suggested management.'
RETURN SELECT COALESCE(MAX(CONCAT(
    upper(severity), ' interaction between ', drug_a, ' and ', drug_b, ': ', effect, '. Management: ', management,
    '. (Demo reference only; confirm with a pharmacist or clinical decision support system.)'
  )), CONCAT('No interaction between ', lower(drug_1), ' and ', lower(drug_2),
             ' in the demo reference table. Absence here does not mean the combination is safe; confirm with a pharmacist.'))
  FROM {ref}.drug_interactions
  WHERE (drug_a = lower(trim(drug_1)) AND drug_b = lower(trim(drug_2)))
     OR (drug_a = lower(trim(drug_2)) AND drug_b = lower(trim(drug_1)))
""")

# COMMAND ----------

# Python UDF: pure computation, no table access (UC Python functions run in a sandbox).
spark.sql(f"""
CREATE OR REPLACE FUNCTION {ai}.lace_score(
  length_of_stay_days INT COMMENT 'Length of the index inpatient stay in days',
  acute_admission BOOLEAN COMMENT 'True if the admission was emergent/acute',
  charlson_score INT COMMENT 'Charlson comorbidity score',
  ed_visits_6m INT COMMENT 'Emergency department visits in the prior 6 months'
)
RETURNS STRING
LANGUAGE PYTHON
COMMENT 'Computes the LACE readmission index (0-19) from its four inputs and returns the score with its risk band (low < 5, moderate 5-9, high >= 10).'
AS $$
los = length_of_stay_days or 0
if los < 1: l = 0
elif los <= 3: l = los
elif los <= 6: l = 4
elif los <= 13: l = 5
else: l = 7
a = 3 if acute_admission else 0
cs = charlson_score or 0
c = cs if cs <= 3 else 5
e = min(ed_visits_6m or 0, 4)
score = l + a + c + e
band = "high" if score >= 10 else ("moderate" if score >= 5 else "low")
return f"LACE {{score}} ({{band}} risk): L={{l}}, A={{a}}, C={{c}}, E={{e}}"
$$
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {ai}.calculate_readmission_risk(
  target_patient_id STRING COMMENT 'Patient ID in the form PAT-000123'
)
RETURNS STRING
COMMENT 'Calculates the LACE 30-day readmission risk score for ONE patient based on their most recent inpatient stay, comorbidities and ED use. Use for "what is the readmission risk for PAT-..." questions.'
RETURN SELECT COALESCE(MAX(
    CASE WHEN last_inpatient_los IS NULL
      THEN CONCAT('Patient ', patient_id, ' has no inpatient stay on record, so LACE does not apply.')
      ELSE CONCAT('Patient ', patient_id, ' (last discharge ', CAST(last_discharge_date AS STRING), '): ',
                  {ai}.lace_score(CAST(last_inpatient_los AS INT), TRUE, CAST(charlson_score AS INT), CAST(ed_visits_6m AS INT)))
    END), CONCAT('No patient found with ID ', target_patient_id))
  FROM {gold}.patient_risk_features
  WHERE patient_id = upper(trim(target_patient_id))
""")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Governance (optional, recommended for real PHI)
# MAGIC Column mask on the identified dimension so only care managers see names and birth dates.
# MAGIC Uncomment once the `hc-care-managers` group exists.

# COMMAND ----------

# spark.sql(f"""
# CREATE OR REPLACE FUNCTION {ai}.mask_phi(value STRING)
# RETURNS STRING
# RETURN CASE WHEN is_account_group_member('hc-care-managers') THEN value ELSE '***' END
# """)
# spark.sql(f"ALTER TABLE {gold}.dim_patient ALTER COLUMN last_name SET MASK {ai}.mask_phi")
# spark.sql(f"ALTER TABLE {gold}.dim_patient ALTER COLUMN first_name SET MASK {ai}.mask_phi")

# COMMAND ----------

for q in [
    f"SELECT {ai}.get_patient_summary('PAT-000001')",
    f"SELECT {ai}.check_prior_auth('72148', 'PAY-01')",
    f"SELECT {ai}.get_formulary_status('semaglutide', 'PAY-03')",
    f"SELECT {ai}.check_drug_interaction('ibuprofen', 'warfarin')",
    f"SELECT {ai}.calculate_readmission_risk('PAT-000002')",
]:
    print(spark.sql(q).first()[0])
