-- One row per encounter; latest load wins if an extract is re-sent.
with ranked as (
    select
        *,
        row_number() over (partition by encounter_id order by _loaded_at desc) as rn
    from {{ source('bronze', 'encounters') }}
)
select
    encounter_id,
    patient_id,
    provider_id,
    lower(encounter_type)                                         as encounter_type,
    cast(admit_ts as timestamp)                                   as admit_ts,
    cast(discharge_ts as timestamp)                               as discharge_ts,
    cast(admit_ts as date)                                        as admit_date,
    cast(discharge_ts as date)                                    as discharge_date,
    datediff(cast(discharge_ts as date), cast(admit_ts as date))  as length_of_stay_days,
    facility,
    primary_dx_code,
    lower(discharge_disposition)                                  as discharge_disposition,
    _loaded_at
from ranked
where rn = 1
