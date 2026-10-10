{#
  Loss experience by state and calendar year: frequency, severity, loss cost, and a
  paid-to-earned-premium proxy.

  Definitions (see docs/INTERVIEW_GUIDE.md):
    frequency_per_1000   = paid claims / earned policy-years x 1000
    severity             = total paid / paid claims
    loss_cost            = total paid / earned policy-years   (= frequency x severity)
    paid_to_premium      = total paid / earned premium  -- a PROXY, not a statutory loss ratio:
                           paid amounts are not developed to ultimate, exclude loss-adjustment
                           expense, and premium excludes fees/surcharges.
  Exposure-based measures are only populated for years with complete earned exposure.
#}
with claims as (
    select
        g.state_abbr,
        f.loss_year                                         as calendar_year,
        count(*)                                            as claim_count,
        count(*) filter (where f.is_paid_claim)             as paid_claim_count,
        sum(f.total_paid)                                   as total_paid,
        sum(f.total_paid_real)                              as total_paid_real,
        sum(f.building_paid)                                as building_paid,
        sum(f.contents_paid)                                as contents_paid,
        sum(f.icc_paid)                                     as icc_paid
    from {{ ref('fact_claims') }} as f
    join {{ ref('dim_geography') }} as g using (geography_key)
    group by all
),

exposure as (
    select
        g.state_abbr,
        e.calendar_year,
        bool_and(e.is_complete_exposure_year)               as is_complete_exposure_year,
        sum(e.earned_policy_years)                          as earned_policy_years,
        sum(e.earned_premium)                               as earned_premium,
        sum(e.building_coverage_in_force_years)             as building_coverage_in_force_years
    from {{ ref('fact_policy_exposure') }} as e
    join {{ ref('dim_geography') }} as g using (geography_key)
    group by all
),

cpi as (
    select calendar_year, to_base_year_factor from {{ ref('stg_fred__cpi_annual') }}
)

select
    coalesce(c.state_abbr, e.state_abbr)                    as state_abbr,
    coalesce(c.calendar_year, e.calendar_year)              as calendar_year,
    coalesce(c.claim_count, 0)                              as claim_count,
    coalesce(c.paid_claim_count, 0)                         as paid_claim_count,
    coalesce(c.total_paid, 0)                               as total_paid,
    coalesce(c.total_paid_real, 0)                          as total_paid_real,
    coalesce(c.building_paid, 0)                            as building_paid,
    coalesce(c.contents_paid, 0)                            as contents_paid,
    coalesce(c.icc_paid, 0)                                 as icc_paid,
    coalesce(e.is_complete_exposure_year, false)            as is_complete_exposure_year,
    case when e.is_complete_exposure_year then e.earned_policy_years end       as earned_policy_years,
    case when e.is_complete_exposure_year then e.earned_premium end            as earned_premium,
    case when e.is_complete_exposure_year then e.earned_premium * cpi.to_base_year_factor end as earned_premium_real,
    case when e.is_complete_exposure_year and e.earned_policy_years > 0
        then 1000.0 * coalesce(c.paid_claim_count, 0) / e.earned_policy_years end as frequency_per_1000,
    case when c.paid_claim_count > 0 then c.total_paid / c.paid_claim_count end   as severity,
    case when c.paid_claim_count > 0 then c.total_paid_real / c.paid_claim_count end as severity_real,
    case when e.is_complete_exposure_year and e.earned_policy_years > 0
        then coalesce(c.total_paid, 0) / e.earned_policy_years end                as loss_cost,
    case when e.is_complete_exposure_year and e.earned_policy_years > 0
        then coalesce(c.total_paid_real, 0) / e.earned_policy_years end           as loss_cost_real,
    case when e.is_complete_exposure_year and e.earned_premium > 0
        then coalesce(c.total_paid, 0) / e.earned_premium end                     as paid_to_premium_proxy
from claims as c
full outer join exposure as e
    on c.state_abbr = e.state_abbr and c.calendar_year = e.calendar_year
left join cpi on coalesce(c.calendar_year, e.calendar_year) = cpi.calendar_year
