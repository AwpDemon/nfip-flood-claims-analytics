{# Repetitive-loss exhibit from NFIP Multiple Loss Properties (one row per state). #}
select
    coalesce(state_abbr, 'Unknown')                                 as state_abbr,
    count(*)                                                        as multiple_loss_properties,
    count(*) filter (where is_nfip_repetitive_loss)                 as nfip_repetitive_loss_properties,
    count(*) filter (where is_nfip_severe_repetitive_loss)          as nfip_severe_repetitive_loss_properties,
    avg(case when is_mitigated then 1.0 else 0.0 end)               as share_mitigated,
    avg(case when is_currently_insured then 1.0 else 0.0 end)       as share_currently_insured,
    avg(total_losses)                                               as avg_total_losses,
    max(total_losses)                                               as max_total_losses,
    avg(case when flood_zone like 'A%' or flood_zone like 'V%' then 1.0 else 0.0 end) as share_in_sfha
from {{ ref('stg_openfema__multiple_loss_properties') }}
group by 1
