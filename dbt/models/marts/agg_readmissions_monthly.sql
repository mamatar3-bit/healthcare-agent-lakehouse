-- 30-day all-cause readmission rate by discharge month. Index admissions exclude
-- patients who expired, and the most recent 30 days (outcome not yet known).
select
    date_trunc('month', e.discharge_date)            as discharge_month,
    e.facility,
    py.payer_type,
    e.primary_dx_category,
    count(*)                                         as index_admissions,
    count_if(e.readmitted_within_30d)                as readmissions_30d,
    round(count_if(e.readmitted_within_30d) / count(*), 4) as readmission_rate_30d
from {{ ref('fct_encounters') }} e
left join {{ ref('payers') }} py on py.payer_id = e.payer_id
where e.encounter_type = 'inpatient'
  and e.discharge_disposition <> 'expired'
  and e.discharge_date <= date_sub(current_date(), {{ var('readmission_window_days') }})
group by all
