{#
  Exposure fact: earned policy-years and earned premium.
  Grain: geography_key x calendar_year x flood_zone_key x occupancy_key.
  `period_date_key` (January 1 of the year) lets BI tools relate this year-grain fact to dim_date.
#}
select
    md5(geography_key || '|' || calendar_year || '|' || flood_zone_key || '|' || occupancy_key) as exposure_key,
    geography_key,
    make_date(calendar_year, 1, 1) as period_date_key,
    calendar_year,
    flood_zone_key,
    occupancy_key,
    is_complete_exposure_year,
    earned_policy_years,
    earned_insured_unit_years,
    earned_premium,
    building_coverage_in_force_years,
    policy_terms_written
from {{ ref('int_policy_earned_exposure') }}
