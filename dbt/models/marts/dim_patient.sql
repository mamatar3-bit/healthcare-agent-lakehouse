-- Identified patient dimension. Restricted: grant only to care-management roles.
-- Analytics and Genie use dim_patient_deid instead (HIPAA minimum necessary).
select
    p.patient_id,
    p.first_name,
    p.last_name,
    p.birth_date,
    p.age_years,
    p.age_band,
    p.sex,
    p.zip,
    p.zip3,
    p.member_id,
    p.payer_id,
    py.payer_name,
    py.payer_type,
    p.pcp_provider_id,
    pr.provider_name as pcp_name,
    p.created_at
from {{ ref('stg_patients') }} p
left join {{ ref('payers') }} py on py.payer_id = p.payer_id
left join {{ ref('stg_providers') }} pr on pr.provider_id = p.pcp_provider_id
