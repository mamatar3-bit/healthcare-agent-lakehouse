select
    patient_id,
    first_name,
    last_name,
    cast(birth_date as date)                                    as birth_date,
    floor(months_between(current_date(), birth_date) / 12)     as age_years,
    case
        when months_between(current_date(), birth_date) / 12 < 18 then '0-17'
        when months_between(current_date(), birth_date) / 12 < 35 then '18-34'
        when months_between(current_date(), birth_date) / 12 < 50 then '35-49'
        when months_between(current_date(), birth_date) / 12 < 65 then '50-64'
        when months_between(current_date(), birth_date) / 12 < 80 then '65-79'
        else '80+'
    end                                                         as age_band,
    upper(sex)                                                  as sex,
    zip,
    left(zip, 3)                                                as zip3,
    payer_id,
    member_id,
    pcp_provider_id,
    cast(created_at as timestamp)                               as created_at
from {{ source('bronze', 'patients') }}
