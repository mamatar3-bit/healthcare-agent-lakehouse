select
    provider_id,
    npi,
    provider_name,
    specialty,
    facility,
    state
from {{ source('bronze', 'providers') }}
