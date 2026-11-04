select
    l.*,
    date_trunc('month', l.collected_ts) as collected_month
from {{ ref('stg_lab_results') }} l
