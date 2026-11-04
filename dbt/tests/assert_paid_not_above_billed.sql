select claim_id, billed_amount, paid_amount
from {{ ref('fct_claims') }}
where paid_amount > billed_amount
