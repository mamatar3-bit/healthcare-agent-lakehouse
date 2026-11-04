select
    e.admit_month                                   as month,
    e.facility,
    py.payer_type,
    e.encounter_type,
    count(*)                                        as encounters,
    count(distinct e.patient_id)                    as unique_patients,
    round(avg(case when e.encounter_type = 'inpatient' then e.length_of_stay_days end), 2) as avg_inpatient_los_days,
    sum(c.paid)                                     as total_paid_amount,
    sum(c.billed)                                   as total_billed_amount
from {{ ref('fct_encounters') }} e
left join {{ ref('payers') }} py on py.payer_id = e.payer_id
left join (
    select encounter_id, sum(paid_amount) as paid, sum(billed_amount) as billed
    from {{ ref('fct_claims') }}
    group by encounter_id
) c on c.encounter_id = e.encounter_id
group by all
