{# One row per rated flood-zone code seen in claims or policies (plus an UNKNOWN member). #}
with codes as (
    select distinct flood_zone_key as flood_zone_code from {{ ref('int_claims_enriched') }}
    union
    select distinct flood_zone_key from {{ ref('int_policy_earned_exposure') }}
    union
    select 'UNKNOWN'
)

select
    flood_zone_code                                                         as flood_zone_key,
    flood_zone_code,
    {{ flood_zone_group("nullif(flood_zone_code, 'UNKNOWN')") }}             as flood_zone_group,
    {{ flood_zone_detail("nullif(flood_zone_code, 'UNKNOWN')") }}            as flood_zone_detail,
    {{ is_sfha("nullif(flood_zone_code, 'UNKNOWN')") }}                      as is_sfha,
    case
        when flood_zone_code like 'V%' then 'Coastal high hazard: wave action of 3 ft+ in the 1%-annual-chance flood'
        when flood_zone_code like 'A%' then '1%-annual-chance (100-year) flood area; mandatory purchase applies to federally backed mortgages'
        when flood_zone_code in ('X', 'B', 'C') then 'Outside the SFHA: moderate (0.2%) or minimal flood hazard'
        when flood_zone_code = 'D' then 'Possible but undetermined flood hazard'
        else 'Zone not reported on the claim'
    end                                                                     as zone_meaning
from codes
