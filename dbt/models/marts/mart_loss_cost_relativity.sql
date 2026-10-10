{#
  Credibility-weighted loss-cost index by state x flood-zone group, over the complete exposure
  window ({{ var('exposure_start_year') }}-{{ var('exposure_end_year') }}), in constant dollars.

  raw_relativity       = cell loss cost / national loss cost for the same zone group
  credibility (Z)      = min(1, sqrt(paid claims / 1082))     -- limited-fluctuation ("square-root")
                         rule; 1,082 claims gives 90% probability of being within 5% of the true
                         frequency under a Poisson assumption (classic full-credibility standard).
  credibility_weighted = Z x raw_relativity + (1 - Z) x 1.0   -- shrink thin cells to the zone mean

  Paid amounts for the latest loss years are not fully developed; this index is a loss-cost
  PROXY for ranking, not a rate indication.
#}
with claims as (
    select
        g.state_abbr,
        z.flood_zone_group,
        count(*) filter (where f.is_paid_claim)     as paid_claim_count,
        sum(f.total_paid_real)                      as total_paid_real
    from {{ ref('fact_claims') }} as f
    join {{ ref('dim_geography') }} as g using (geography_key)
    join {{ ref('dim_flood_zone') }} as z using (flood_zone_key)
    where f.loss_year between {{ var('exposure_start_year') }} and {{ var('exposure_end_year') }}
    group by all
),

exposure as (
    select
        g.state_abbr,
        z.flood_zone_group,
        sum(e.earned_policy_years)                  as earned_policy_years,
        sum(e.earned_premium)                       as earned_premium
    from {{ ref('fact_policy_exposure') }} as e
    join {{ ref('dim_geography') }} as g using (geography_key)
    join {{ ref('dim_flood_zone') }} as z using (flood_zone_key)
    where e.is_complete_exposure_year
    group by all
),

cells as (
    select
        e.state_abbr,
        e.flood_zone_group,
        e.earned_policy_years,
        e.earned_premium,
        coalesce(c.paid_claim_count, 0)             as paid_claim_count,
        coalesce(c.total_paid_real, 0)              as total_paid_real
    from exposure as e
    left join claims as c using (state_abbr, flood_zone_group)
    where e.earned_policy_years > 0
),

national as (
    select
        flood_zone_group,
        sum(total_paid_real) / sum(earned_policy_years) as national_loss_cost_real
    from cells
    group by 1
)

select
    c.state_abbr,
    c.flood_zone_group,
    c.earned_policy_years,
    c.paid_claim_count,
    c.total_paid_real,
    1000.0 * c.paid_claim_count / c.earned_policy_years                  as frequency_per_1000,
    c.total_paid_real / c.earned_policy_years                            as loss_cost_real,
    n.national_loss_cost_real,
    (c.total_paid_real / c.earned_policy_years) / nullif(n.national_loss_cost_real, 0) as raw_relativity,
    least(1.0, sqrt(c.paid_claim_count / 1082.0))                        as credibility_z,
    least(1.0, sqrt(c.paid_claim_count / 1082.0))
        * ((c.total_paid_real / c.earned_policy_years) / nullif(n.national_loss_cost_real, 0))
        + (1 - least(1.0, sqrt(c.paid_claim_count / 1082.0))) * 1.0     as credibility_weighted_relativity
from cells as c
join national as n using (flood_zone_group)
