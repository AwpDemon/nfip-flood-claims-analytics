select
    occupancy_code          as occupancy_key,
    occupancy_code,
    occupancy_description,
    occupancy_group,
    occupancy_code >= 11    as is_risk_rating_2_code
from {{ ref('ref_occupancy_types') }}
union all
select -1, -1, 'Not reported', 'Unknown', false
