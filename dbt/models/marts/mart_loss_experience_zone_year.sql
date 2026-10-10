{# Loss experience by flood-zone group and calendar year (same definitions as the state mart). #}
with claims as (
    select
        z.flood_zone_group,
        f.loss_year                                         as calendar_year,
        count(*)                                            as claim_count,
        count(*) filter (where f.is_paid_claim)             as paid_claim_count,
        sum(f.total_paid)                                   as total_paid,
        sum(f.total_paid_real)                              as total_paid_real
    from {{ ref('fact_claims') }} as f
    join {{ ref('dim_flood_zone') }} as z using (flood_zone_key)
    group by all
),

exposure as (
    select
        z.flood_zone_group,
        e.calendar_year,
        bool_and(e.is_complete_exposure_year)               as is_complete_exposure_year,
        sum(e.earned_policy_years)                          as earned_policy_years,
        sum(e.earned_premium)                               as earned_premium
    from {{ ref('fact_policy_exposure') }} as e
    join {{ ref('dim_flood_zone') }} as z using (flood_zone_key)
    group by all
)

select
    coalesce(c.flood_zone_group, e.flood_zone_group)        as flood_zone_group,
    coalesce(c.calendar_year, e.calendar_year)              as calendar_year,
    coalesce(c.claim_count, 0)                              as claim_count,
    coalesce(c.paid_claim_count, 0)                         as paid_claim_count,
    coalesce(c.total_paid, 0)                               as total_paid,
    coalesce(c.total_paid_real, 0)                          as total_paid_real,
    coalesce(e.is_complete_exposure_year, false)            as is_complete_exposure_year,
    case when e.is_complete_exposure_year then e.earned_policy_years end as earned_policy_years,
    case when e.is_complete_exposure_year then e.earned_premium end      as earned_premium,
    case when e.is_complete_exposure_year and e.earned_policy_years > 0
        then 1000.0 * coalesce(c.paid_claim_count, 0) / e.earned_policy_years end as frequency_per_1000,
    case when c.paid_claim_count > 0 then c.total_paid_real / c.paid_claim_count end as severity_real,
    case when e.is_complete_exposure_year and e.earned_policy_years > 0
        then coalesce(c.total_paid, 0) / e.earned_policy_years end                as loss_cost,
    case when e.is_complete_exposure_year and e.earned_premium > 0
        then coalesce(c.total_paid, 0) / e.earned_premium end                     as paid_to_premium_proxy
from claims as c
full outer join exposure as e
    on c.flood_zone_group = e.flood_zone_group and c.calendar_year = e.calendar_year
