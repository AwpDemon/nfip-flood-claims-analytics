{#
  Earned exposure and earned premium by calendar year.

  A policy term that runs 2019-06-01 -> 2020-06-01 contributes ~0.58 policy-years to 2019
  and ~0.42 to 2020 (pro-rata by days), and its premium is earned the same way. This is
  the standard actuarial "earned" view and is what claim frequency must be divided by:
  claims are counted by date of loss, so exposure must be counted by calendar time too.

  Grain: county (geography_key) x calendar_year x rated flood-zone code x occupancy code,
  i.e. exactly the keys of dim_geography / dim_flood_zone / dim_occupancy so the exposure fact
  conforms with fact_claims.
  The 74.7M-row policy file is streamed through DuckDB; only this aggregate is stored.
#}
with policies as (
    select
        p.*,
        s.state_fips
    from {{ ref('stg_openfema__policies') }} as p
    left join {{ ref('ref_states') }} as s on p.state_abbr = s.state_abbr
    where p.term_end_date > p.policy_effective_date
),

expanded as (
    select
        state_abbr,
        state_fips,
        case when county_fips is not null and substr(county_fips, 1, 2) = state_fips then county_fips end as county_fips_clean,
        rated_flood_zone,
        occupancy_code,
        policy_count,
        premium,
        building_coverage,
        date_diff('day', policy_effective_date, policy_termination_date) as scheduled_term_days,
        policy_effective_date,
        calendar_year,
        greatest(policy_effective_date, make_date(calendar_year, 1, 1)) as earn_start,
        least(term_end_date, make_date(calendar_year + 1, 1, 1))        as earn_end
    from (
        select
            *,
            unnest(generate_series(year(policy_effective_date), year(term_end_date - interval 1 day))) as calendar_year
        from policies
    )
)

select
    state_abbr,
    coalesce(county_fips_clean, 'UNK-' || coalesce(case when state_fips is not null then state_abbr end, 'ZZ')) as geography_key,
    calendar_year,
    coalesce(rated_flood_zone, 'UNKNOWN') as flood_zone_key,
    coalesce(occupancy_code, -1)          as occupancy_key,
    calendar_year between {{ var('exposure_start_year') }} and {{ var('exposure_end_year') }} as is_complete_exposure_year,
    sum(date_diff('day', earn_start, earn_end)) / 365.0                                   as earned_policy_years,
    sum(date_diff('day', earn_start, earn_end) * coalesce(policy_count, 1)) / 365.0       as earned_insured_unit_years,
    sum(premium * date_diff('day', earn_start, earn_end) / nullif(scheduled_term_days, 0)) as earned_premium,
    sum(building_coverage * date_diff('day', earn_start, earn_end)) / 365.0               as building_coverage_in_force_years,
    count(*) filter (where year(policy_effective_date) = calendar_year)                   as policy_terms_written
from expanded
group by all
