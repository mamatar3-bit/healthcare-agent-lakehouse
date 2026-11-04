with ranked as (
    select
        *,
        row_number() over (partition by claim_id order by _loaded_at desc) as rn
    from {{ source('bronze', 'claims') }}
)
select
    claim_id,
    encounter_id,
    patient_id,
    payer_id,
    cast(cpt_code as string)                   as cpt_code,
    cast(billed_amount as decimal(12, 2))      as billed_amount,
    cast(allowed_amount as decimal(12, 2))     as allowed_amount,
    cast(paid_amount as decimal(12, 2))        as paid_amount,
    lower(claim_status)                        as claim_status,
    denial_reason,
    left(denial_reason, instr(concat(denial_reason, ' '), ' ') - 1) as denial_code,
    cast(submitted_date as date)               as submitted_date,
    cast(adjudicated_date as date)             as adjudicated_date,
    _loaded_at
from ranked
where rn = 1
