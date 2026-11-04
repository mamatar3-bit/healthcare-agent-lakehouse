{% set window = var('readmission_window_days') %}

with enc as (
    select
        e.*,
        dx.dx_description as primary_dx_description,
        dx.dx_category    as primary_dx_category,
        p.payer_id
    from {{ ref('stg_encounters') }} e
    left join {{ ref('icd10_codes') }} dx on dx.dx_code = e.primary_dx_code
    left join {{ ref('stg_patients') }} p on p.patient_id = e.patient_id
),

inpatient as (
    select
        encounter_id,
        lead(admit_date)   over (partition by patient_id order by admit_ts) as next_admit_date,
        lag(discharge_date) over (partition by patient_id order by admit_ts) as prev_discharge_date
    from enc
    where encounter_type = 'inpatient'
)

select
    enc.encounter_id,
    enc.patient_id,
    enc.provider_id,
    enc.payer_id,
    enc.encounter_type,
    enc.facility,
    enc.admit_ts,
    enc.discharge_ts,
    enc.admit_date,
    enc.discharge_date,
    date_trunc('month', enc.admit_date)                        as admit_month,
    enc.length_of_stay_days,
    enc.primary_dx_code,
    enc.primary_dx_description,
    enc.primary_dx_category,
    enc.discharge_disposition,
    coalesce(
        enc.encounter_type = 'inpatient'
        and enc.discharge_disposition <> 'expired'
        and datediff(i.next_admit_date, enc.discharge_date) between 0 and {{ window }},
        false
    )                                                          as readmitted_within_30d,
    coalesce(
        enc.encounter_type = 'inpatient'
        and datediff(enc.admit_date, i.prev_discharge_date) between 0 and {{ window }},
        false
    )                                                          as is_readmission
from enc
left join inpatient i on i.encounter_id = enc.encounter_id
