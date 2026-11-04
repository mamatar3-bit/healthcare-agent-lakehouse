select
    submitted_month,
    payer_id,
    payer_name,
    payer_type,
    cpt_category,
    count(*)                                          as claims,
    count_if(is_denied)                               as denied_claims,
    round(count_if(is_denied) / count(*), 4)          as denial_rate,
    sum(case when is_denied then billed_amount end)   as denied_billed_amount,
    count_if(is_denied and denial_code = 'CO-197')    as prior_auth_denials
from {{ ref('fct_claims') }}
group by all
