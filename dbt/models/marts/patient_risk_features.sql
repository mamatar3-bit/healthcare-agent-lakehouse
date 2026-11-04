-- One row per patient: inputs to the LACE readmission score and the patient summary tool.
with dx as (
    select
        e.patient_id,
        count(distinct case when i.is_chronic then d.dx_code end)              as chronic_condition_count,
        concat_ws(', ', sort_array(collect_set(case when i.is_chronic then i.dx_description end))) as chronic_conditions
    from {{ ref('stg_diagnoses') }} d
    join {{ ref('stg_encounters') }} e on e.encounter_id = d.encounter_id
    join {{ ref('icd10_codes') }} i on i.dx_code = d.dx_code
    group by e.patient_id
),

dx_dedup as (
    -- charlson: count each condition once per patient
    select patient_id, sum(charlson_weight) as charlson_score
    from (
        select distinct e.patient_id, i.dx_code, i.charlson_weight
        from {{ ref('stg_diagnoses') }} d
        join {{ ref('stg_encounters') }} e on e.encounter_id = d.encounter_id
        join {{ ref('icd10_codes') }} i on i.dx_code = d.dx_code
    )
    group by patient_id
),

util as (
    select
        patient_id,
        count_if(encounter_type = 'emergency' and admit_date >= add_months(current_date(), -6))  as ed_visits_6m,
        count_if(encounter_type = 'inpatient' and admit_date >= add_months(current_date(), -12)) as inpatient_admits_12m,
        max(case when encounter_type = 'inpatient' then discharge_date end)                      as last_discharge_date,
        max(admit_date)                                                                           as last_encounter_date
    from {{ ref('fct_encounters') }}
    group by patient_id
),

last_ip as (
    select patient_id, length_of_stay_days as last_inpatient_los, readmitted_within_30d as last_ip_readmitted,
           discharge_disposition as last_discharge_disposition
    from {{ ref('fct_encounters') }}
    where encounter_type = 'inpatient'
    qualify row_number() over (partition by patient_id order by admit_ts desc) = 1
),

a1c as (
    select patient_id, result_value as last_a1c, cast(collected_ts as date) as last_a1c_date
    from {{ ref('stg_lab_results') }}
    where loinc_code = '4548-4'
    qualify row_number() over (partition by patient_id order by collected_ts desc) = 1
),

meds as (
    select patient_id, concat_ws(', ', sort_array(collect_set(drug_name))) as active_medications
    from {{ ref('fct_prescriptions') }}
    where is_active
    group by patient_id
),

denials as (
    select patient_id, count(*) as open_denied_claims
    from {{ ref('fct_claims') }}
    where claim_status = 'denied'
    group by patient_id
)

select
    p.patient_id,
    p.age_band,
    p.sex,
    p.payer_id,
    p.payer_type,
    p.pcp_provider_id,
    coalesce(dx.chronic_condition_count, 0)   as chronic_condition_count,
    coalesce(dd.charlson_score, 0)            as charlson_score,
    dx.chronic_conditions,
    coalesce(u.ed_visits_6m, 0)               as ed_visits_6m,
    coalesce(u.inpatient_admits_12m, 0)       as inpatient_admits_12m,
    u.last_discharge_date,
    u.last_encounter_date,
    li.last_inpatient_los,
    li.last_ip_readmitted,
    li.last_discharge_disposition,
    a1c.last_a1c,
    a1c.last_a1c_date,
    meds.active_medications,
    coalesce(den.open_denied_claims, 0)       as open_denied_claims
from {{ ref('dim_patient_deid') }} p
left join dx       on dx.patient_id = p.patient_id
left join dx_dedup dd on dd.patient_id = p.patient_id
left join util u   on u.patient_id = p.patient_id
left join last_ip li on li.patient_id = p.patient_id
left join a1c      on a1c.patient_id = p.patient_id
left join meds     on meds.patient_id = p.patient_id
left join denials den on den.patient_id = p.patient_id
