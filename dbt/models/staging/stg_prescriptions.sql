select
    rx_id,
    patient_id,
    provider_id,
    encounter_id,
    lower(trim(drug_name))     as drug_name,
    cast(days_supply as int)   as days_supply,
    cast(refills as int)       as refills,
    cast(fill_date as date)    as fill_date
from {{ source('bronze', 'prescriptions') }}
