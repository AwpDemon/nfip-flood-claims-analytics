select
    cause_code          as cause_key,
    cause_code,
    cause_description,
    cause_group
from {{ ref('ref_cause_of_damage') }}
union all
select 'UNK', 'UNK', 'Not reported or not a valid code', 'Unknown'
