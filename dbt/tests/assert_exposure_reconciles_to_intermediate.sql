-- Earned exposure and premium must be identical in the intermediate aggregate and the fact.
with i as (
    select sum(earned_policy_years) as py, sum(earned_premium) as prem from {{ ref('int_policy_earned_exposure') }}
),
f as (
    select sum(earned_policy_years) as py, sum(earned_premium) as prem from {{ ref('fact_policy_exposure') }}
)
select i.py, f.py as fact_py, i.prem, f.prem as fact_prem
from i, f
where abs(i.py - f.py) > 0.001 or abs(i.prem - f.prem) > 0.01
