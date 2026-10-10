{#
  NFIP policy-term transactions, typed and trimmed to the columns needed for exposure.
  Kept as a view: the full table is ~74.7M rows and is only ever aggregated downstream.

  Term end = the earlier of the scheduled termination date and any cancellation date.
#}
select
    cast(id as bigint)                                        as policy_record_id,
    upper(trim(propertyState))                                as state_abbr,
    nullif(substr(trim(censusGeoid), 1, 5), '')               as county_fips,
    nullif(upper(trim(ratedFloodZone)), '')                   as rated_flood_zone,
    cast(occupancyType as integer)                            as occupancy_code,
    cast(policyEffectiveDate as date)                         as policy_effective_date,
    cast(policyTerminationDate as date)                       as policy_termination_date,
    cast(cancellationDateOfFloodPolicy as date)               as cancellation_date,
    least(
        cast(policyTerminationDate as date),
        coalesce(cast(cancellationDateOfFloodPolicy as date), cast(policyTerminationDate as date))
    )                                                         as term_end_date,
    cast(policyCount as integer)                              as policy_count,
    cast(totalInsurancePremiumOfThePolicy as double)          as premium,
    cast(policyCost as double)                                as policy_cost,
    cast(totalBuildingInsuranceCoverage as double)            as building_coverage,
    cast(totalContentsInsuranceCoverage as double)            as contents_coverage,
    cast(primaryResidenceIndicator as boolean)                as is_primary_residence,
    cast(postFIRMConstructionIndicator as boolean)            as is_post_firm
from {{ source('openfema', 'nfip_policies') }}
