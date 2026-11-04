select distinct
    encounter_id,
    dx_code,
    cast(dx_rank as int) as dx_rank
from {{ source('bronze', 'diagnoses') }}
