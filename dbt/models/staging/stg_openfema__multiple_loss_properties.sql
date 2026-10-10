select
    cast(id as varchar)                                 as property_id,
    upper(trim(stateAbbreviation))                      as state_abbr,
    nullif(trim(fipsCountyCode), '')                    as county_fips,
    nullif(upper(trim(floodZone)), '')                  as flood_zone,
    cast(occupancyType as integer)                      as occupancy_code,
    cast(nfipRl as boolean)                             as is_nfip_repetitive_loss,
    cast(nfipSrl as boolean)                            as is_nfip_severe_repetitive_loss,
    cast(fmaRl as boolean)                              as is_fma_repetitive_loss,
    cast(fmaSrl as boolean)                             as is_fma_severe_repetitive_loss,
    cast(mitigatedIndicator as boolean)                 as is_mitigated,
    cast(insuredIndicator as boolean)                   as is_currently_insured,
    cast(primaryResidenceIndicator as boolean)          as is_primary_residence,
    cast(postFIRMConstructionIndicator as boolean)      as is_post_firm,
    cast(totalLosses as integer)                        as total_losses,
    cast(mostRecentDateofLoss as date)                  as most_recent_date_of_loss,
    cast(originalConstructionDate as date)              as original_construction_date
from {{ source('openfema', 'nfip_multiple_loss_properties') }}
