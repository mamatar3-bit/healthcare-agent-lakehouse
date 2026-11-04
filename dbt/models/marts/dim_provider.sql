select
    p.provider_id,
    p.npi,
    p.provider_name,
    p.specialty,
    p.facility,
    p.state,
    count(distinct pt.patient_id) as panel_size
from {{ ref('stg_providers') }} p
left join {{ ref('stg_patients') }} pt on pt.pcp_provider_id = p.provider_id
group by all
