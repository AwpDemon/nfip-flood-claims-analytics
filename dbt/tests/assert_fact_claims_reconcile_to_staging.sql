-- Row count and paid dollars must survive staging -> intermediate -> fact unchanged.
-- Returns a row (= failure) if either the claim count or total paid differs by more than one cent.
with stg as (
    select count(*) as n, sum(total_paid) as paid from {{ ref('stg_openfema__claims') }}
),
fct as (
    select count(*) as n, sum(total_paid) as paid from {{ ref('fact_claims') }}
)
select stg.n as stg_rows, fct.n as fact_rows, stg.paid as stg_paid, fct.paid as fact_paid
from stg, fct
where stg.n <> fct.n or abs(stg.paid - fct.paid) > 0.01
