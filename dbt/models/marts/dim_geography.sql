{#
  County-grain geography with explicit "unknown county" members per state.

  Members:
    * every county in the 2020 Census FIPS list,
    * one 'UNK-<state>' member per state for claims/policies whose county is missing or invalid,
    * any county code observed in the data but not in the 2020 list (e.g. Connecticut's 2022
      planning regions), labelled as such - so relationships tests never silently drop rows.
#}
with census as (
    select
        c.county_fips                 as geography_key,
        c.county_fips,
        c.county_name,
        c.state_abbr,
        c.state_fips,
        s.state_name,
        'census_2020'                 as member_source
    from {{ ref('ref_counties') }} as c
    left join {{ ref('ref_states') }} as s on c.state_fips = s.state_fips
),

unknown_members as (
    select
        'UNK-' || state_abbr          as geography_key,
        cast(null as varchar)         as county_fips,
        'Unknown county'              as county_name,
        state_abbr,
        state_fips,
        state_name,
        'unknown_member'              as member_source
    from {{ ref('ref_states') }}
    union all
    select 'UNK-ZZ', null, 'Unknown county', 'ZZ', null, 'Unknown state', 'unknown_member'
),

observed as (
    select distinct geography_key from {{ ref('int_claims_enriched') }}
    union
    select distinct geography_key from {{ ref('int_policy_earned_exposure') }}
),

unlisted as (
    select
        o.geography_key,
        o.geography_key               as county_fips,
        'County FIPS ' || o.geography_key || ' (not in 2020 Census list)' as county_name,
        s.state_abbr,
        s.state_fips,
        s.state_name,
        'observed_unlisted'           as member_source
    from observed as o
    left join {{ ref('ref_states') }} as s on substr(o.geography_key, 1, 2) = s.state_fips
    where o.geography_key not like 'UNK-%'
      and o.geography_key not in (select geography_key from census)
),

all_members as (
    select * from census
    union all
    select * from unknown_members
    union all
    select * from unlisted
)

select
    geography_key,
    county_fips,
    county_name,
    state_abbr,
    state_fips,
    state_name,
    state_abbr in ('AL', 'FL', 'GA', 'LA', 'MS', 'NC', 'SC', 'TX', 'VA', 'MD', 'DE', 'NJ', 'NY', 'CT', 'RI', 'MA', 'NH', 'ME', 'PR', 'VI')
        as is_atlantic_gulf_coastal_state,
    member_source
from all_members
