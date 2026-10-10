-- Metric identity: loss cost = frequency x severity. Catches unit / denominator mistakes
-- (e.g. per-1000 scaling, nominal vs real) in the state-year mart.
select
    state_abbr,
    calendar_year,
    loss_cost,
    frequency_per_1000 * severity / 1000.0 as implied_loss_cost
from {{ ref('mart_loss_experience_state_year') }}
where is_complete_exposure_year
  and paid_claim_count > 0
  and abs(loss_cost - frequency_per_1000 * severity / 1000.0) > 0.01
