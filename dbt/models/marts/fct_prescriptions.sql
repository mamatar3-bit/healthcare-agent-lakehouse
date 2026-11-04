select
    rx.rx_id,
    rx.patient_id,
    rx.provider_id,
    rx.encounter_id,
    rx.drug_name,
    rx.days_supply,
    rx.refills,
    rx.fill_date,
    date_add(rx.fill_date, rx.days_supply * (rx.refills + 1))     as supply_end_date,
    date_add(rx.fill_date, rx.days_supply * (rx.refills + 1)) >= current_date() as is_active,
    p.payer_id,
    f.tier                                                      as formulary_tier,
    coalesce(f.requires_prior_auth, false)                      as formulary_prior_auth,
    f.tier is null                                              as is_non_formulary
from {{ ref('stg_prescriptions') }} rx
left join {{ ref('stg_patients') }} p on p.patient_id = rx.patient_id
left join {{ ref('formulary') }} f on f.payer_id = p.payer_id and f.drug_name = rx.drug_name
