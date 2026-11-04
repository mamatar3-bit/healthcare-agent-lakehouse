# Genie space: "Riverbend Population Analytics"

Genie answers the agent's **aggregate, numeric** questions (rates, counts, trends, rankings) by writing SQL
over the de-identified gold tables. Create one space per environment (Dev, QA, Prod) and put its ID in
`databricks.yml` → `targets.<env>.variables.genie_space_id`.

## 1. Create the space (once per environment)
Genie → **New** → select the environment's SQL warehouse → add these tables from `<catalog>.gold`:

| table | why |
|---|---|
| `fct_encounters` | visits, LOS, readmission flags |
| `fct_claims` | billed/paid amounts, denials |
| `fct_prescriptions` | fills, formulary tier |
| `fct_lab_results` | HbA1c, creatinine, potassium |
| `dim_patient_deid` | age band, sex, payer type (no names/DOB) |
| `dim_provider` | specialty, facility, panel size |
| `dim_payer` | plan names and appeal windows |
| `agg_readmissions_monthly` | 30-day readmission rates |
| `agg_monthly_utilization` | monthly volumes and paid amounts |
| `agg_claim_denials_monthly` | denial rates, CO-197 counts |

Do **not** add `dim_patient` (identified). Column descriptions come from dbt (`persist_docs`), so Genie already
knows what each column means.

## 2. General instructions (paste into the space's Instructions)

```
You answer questions about a fictional health network's de-identified data.
- "Readmission rate" means 30-day all-cause readmissions: use agg_readmissions_monthly and compute
  sum(readmissions_30d) / sum(index_admissions). Never average the monthly rates.
- "Denial rate" = denied (denied + appealed) claims / all claims. Use fct_claims.is_denied.
- "Prior auth denial" means denial_code = 'CO-197'.
- Amounts are USD. Round rates to 1 decimal place as a percentage.
- "Last quarter", "this year" etc. are calendar periods relative to current_date().
- Payer types: Commercial, Medicare, Medicaid, Self-pay.
- Never return patient-level lists with more than 50 rows; aggregate instead.
- If a question asks for names, birth dates or addresses, say that data is not available here.
```

## 3. Sample questions (add as sample questions)
- What is the 30-day readmission rate by facility for the last 6 months?
- Which payer has the highest claim denial rate this year?
- How many prior-authorization (CO-197) denials did we have per month in 2026?
- Top 5 primary diagnosis categories by inpatient admissions last quarter
- Average inpatient length of stay by payer type
- What share of diabetic patients have a last HbA1c above 9?

## 4. Trusted SQL (add as example SQL queries)

```sql
-- Readmission rate by facility, trailing 6 months
SELECT facility,
       sum(readmissions_30d) AS readmissions,
       sum(index_admissions) AS index_admissions,
       round(100 * sum(readmissions_30d) / sum(index_admissions), 1) AS readmission_rate_pct
FROM agg_readmissions_monthly
WHERE discharge_month >= add_months(date_trunc('month', current_date()), -6)
GROUP BY facility
ORDER BY readmission_rate_pct DESC;
```

```sql
-- Denial rate by payer, current year
SELECT payer_name,
       count(*) AS claims,
       round(100 * avg(CASE WHEN is_denied THEN 1 ELSE 0 END), 1) AS denial_rate_pct,
       sum(CASE WHEN is_denied THEN billed_amount END) AS denied_billed_usd
FROM fct_claims
WHERE year(submitted_date) = year(current_date())
GROUP BY payer_name
ORDER BY denial_rate_pct DESC;
```

## 5. Grant access
The agent's serving endpoint (or the end user, with on-behalf-of auth) needs **CAN RUN** on the space,
**CAN USE** on the warehouse, and **SELECT** on the tables above. In QA/Prod grant these to the
CI/CD service principal and the `hc-analysts` group.
