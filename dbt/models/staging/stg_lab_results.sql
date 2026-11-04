select
    lab_id,
    patient_id,
    encounter_id,
    loinc_code,
    test_name,
    cast(result_value as double)   as result_value,
    unit,
    cast(ref_low as double)        as ref_low,
    cast(ref_high as double)       as ref_high,
    case
        when result_value < ref_low  then 'low'
        when result_value > ref_high then 'high'
        else 'normal'
    end                            as abnormal_flag,
    cast(collected_ts as timestamp) as collected_ts
from {{ source('bronze', 'lab_results') }}
