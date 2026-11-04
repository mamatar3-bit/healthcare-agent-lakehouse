{{
  config(
    materialized = 'incremental',
    incremental_strategy = 'merge',
    unique_key = 'claim_id',
    on_schema_change = 'append_new_columns'
  )
}}

select
    c.claim_id,
    c.encounter_id,
    c.patient_id,
    c.payer_id,
    py.payer_name,
    py.payer_type,
    c.cpt_code,
    cpt.cpt_description,
    cpt.cpt_category,
    c.billed_amount,
    c.allowed_amount,
    c.paid_amount,
    c.claim_status,
    c.claim_status in ('denied', 'appealed')                   as is_denied,
    c.denial_code,
    c.denial_reason,
    coalesce(pa.requires_prior_auth, false)                    as prior_auth_required,
    c.submitted_date,
    date_trunc('month', c.submitted_date)                      as submitted_month,
    c.adjudicated_date,
    datediff(c.adjudicated_date, c.submitted_date)             as days_to_adjudicate,
    case when c.claim_status in ('denied')
         then date_add(c.adjudicated_date, py.appeal_window_days) end as appeal_deadline,
    c._loaded_at
from {{ ref('stg_claims') }} c
left join {{ ref('payers') }} py on py.payer_id = c.payer_id
left join {{ ref('cpt_codes') }} cpt on cpt.cpt_code = c.cpt_code
left join {{ ref('prior_auth_rules') }} pa on pa.payer_id = c.payer_id and pa.cpt_code = c.cpt_code

{% if is_incremental() %}
where c._loaded_at > (select coalesce(max(_loaded_at), timestamp'1900-01-01') from {{ this }})
{% endif %}
