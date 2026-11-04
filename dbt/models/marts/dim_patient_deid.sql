-- De-identified patient dimension used by Genie and population analytics.
-- No names, birth dates, member IDs or 5-digit ZIPs.
select
    patient_id,
    age_band,
    sex,
    zip3,
    payer_id,
    payer_name,
    payer_type,
    pcp_provider_id
from {{ ref('dim_patient') }}
