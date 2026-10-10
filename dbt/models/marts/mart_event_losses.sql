{# Event league table: every (event name, year) with paid totals in nominal and constant dollars. #}
with agg as (
    select
        event_key,
        count(*)                                                as claim_count,
        count(*) filter (where is_paid_claim)                   as paid_claim_count,
        sum(total_paid)                                         as total_paid,
        sum(total_paid_real)                                    as total_paid_real,
        sum(building_paid)                                      as building_paid,
        sum(contents_paid)                                      as contents_paid,
        sum(icc_paid)                                           as icc_paid,
        median(total_paid) filter (where is_paid_claim)         as median_paid_per_paid_claim,
        median(days_loss_to_last_payment) filter (where is_paid_claim) as median_days_loss_to_last_payment
    from {{ ref('fact_claims') }}
    group by 1
)

select
    e.event_key,
    e.event_label,
    e.event_name,
    e.event_year,
    e.event_type,
    e.is_tropical_cyclone,
    e.primary_state,
    e.states_affected,
    e.first_loss_date,
    e.last_loss_date,
    a.claim_count,
    a.paid_claim_count,
    a.total_paid,
    a.total_paid_real,
    a.building_paid,
    a.contents_paid,
    a.icc_paid,
    a.building_paid / nullif(a.building_paid + a.contents_paid, 0) as building_share_of_paid,
    a.total_paid_real / nullif(a.paid_claim_count, 0)               as severity_real,
    a.median_paid_per_paid_claim,
    a.median_days_loss_to_last_payment,
    rank() over (order by a.total_paid_real desc)                   as rank_by_real_paid
from {{ ref('dim_event') }} as e
join agg as a using (event_key)
