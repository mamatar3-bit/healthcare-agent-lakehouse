-- Singular test: an encounter can't end before it starts.
select encounter_id, admit_ts, discharge_ts
from {{ ref('fct_encounters') }}
where discharge_ts < admit_ts
