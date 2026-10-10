{#
  Flood events derived from the claim-level `floodEvent` name.

  Storm names are re-used across seasons (e.g. "Hurricane Florence" 1988 and 2018), so an event
  is (name, loss year). Claims with no event name roll up to one "(no event name)" member per year.
  Events that straddle New Year split into two members - a known, documented simplification.
#}
with claims as (
    select * from {{ ref('int_claims_enriched') }}
),

events as (
    select
        event_key,
        coalesce(flood_event_name, '(no event name)')         as event_name,
        loss_year                                             as event_year,
        min(date_of_loss)                                     as first_loss_date,
        max(date_of_loss)                                     as last_loss_date,
        count(*)                                              as claim_count,
        count(distinct state_abbr)                            as states_affected,
        mode(state_abbr)                                      as primary_state,
        max(event_designation_number)                         as example_event_designation_number
    from claims
    group by 1, 2, 3
)

select
    event_key,
    event_name,
    event_year,
    event_name || ' (' || event_year || ')'                   as event_label,
    case
        when event_name = '(no event name)' then 'No event name'
        when event_name ilike 'hurricane%' then 'Hurricane'
        when event_name ilike 'tropical storm%' or event_name ilike 'tropical depression%' then 'Tropical storm'
        when event_name ilike '%nor%easter%' then 'Nor''easter'
        else 'Other named flood event'
    end                                                       as event_type,
    event_name ilike 'hurricane%' or event_name ilike 'tropical%' as is_tropical_cyclone,
    first_loss_date,
    last_loss_date,
    date_diff('day', first_loss_date, last_loss_date) + 1     as loss_date_span_days,
    claim_count,
    states_affected,
    primary_state,
    example_event_designation_number
from events
