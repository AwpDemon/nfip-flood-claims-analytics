{#
  Claim-grain business logic: conformed keys, groups, constant-dollar measures,
  cycle-time and data-quality flags. Everything downstream (fact_claims, marts,
  model features) reads from here so the rules live in exactly one place.
#}
with claims as (
    select * from {{ ref('stg_openfema__claims') }}
),

states as (
    select * from {{ ref('ref_states') }}
),

causes as (
    select * from {{ ref('ref_cause_of_damage') }}
),

occupancy as (
    select * from {{ ref('ref_occupancy_types') }}
),

cpi as (
    select * from {{ ref('stg_fred__cpi_annual') }}
),

latest_cpi as (
    -- Loss years without a complete CPI year (the current year) use the latest complete year.
    select to_base_year_factor from cpi order by calendar_year desc limit 1
),

joined as (
    select
        c.*,
        s.state_fips,
        s.state_abbr is not null                                         as is_known_state,
        -- A county code whose state prefix disagrees with the claim's state is treated as unknown:
        -- FEMA notes that county can reflect a project rather than the property.
        case
            when c.county_fips is not null and substr(c.county_fips, 1, 2) = s.state_fips then c.county_fips
        end                                                              as county_fips_clean,
        coalesce(cz.cause_code, 'UNK')                                   as cause_code,
        coalesce(cz.cause_group, 'Unknown')                              as cause_group,
        coalesce(o.occupancy_group, 'Unknown')                           as occupancy_group,
        coalesce(cpi.to_base_year_factor, (select to_base_year_factor from latest_cpi)) as cpi_factor,
        -- 1492-10-12 (Columbus Day) is a placeholder for "unknown", used on ~12% of claims - all
        -- pre-FIRM, loss years 1978-2022. Found by the building-age range test; nulled here.
        nullif(c.original_construction_date, date '1492-10-12')         as construction_date_clean
    from claims as c
    left join states as s on c.state_abbr = s.state_abbr
    left join causes as cz on c.cause_code_raw = cz.cause_code
    left join occupancy as o on c.occupancy_code = o.occupancy_code
    left join cpi on c.loss_year = cpi.calendar_year
)

select
    claim_id,
    record_source,
    source_as_of_ts,

    -- conformed dimension keys
    date_of_loss                                                         as date_key,
    coalesce(county_fips_clean, 'UNK-' || coalesce(case when is_known_state then state_abbr end, 'ZZ')) as geography_key,
    coalesce(rated_flood_zone, 'UNKNOWN')                                as flood_zone_key,
    md5(coalesce(flood_event_name, '(no event name)') || '|' || cast(loss_year as varchar)) as event_key,
    coalesce(occupancy_code, -1)                                         as occupancy_key,
    cause_code                                                           as cause_key,

    -- descriptive attributes kept on the fact for convenience / degenerate dims
    loss_year,
    date_of_loss,
    claim_open_date,
    most_recent_payment_date,
    state_abbr,
    county_fips_clean                                                    as county_fips,
    rated_flood_zone,
    {{ flood_zone_group('rated_flood_zone') }}                           as flood_zone_group,
    {{ flood_zone_detail('rated_flood_zone') }}                          as flood_zone_detail,
    {{ is_sfha('rated_flood_zone') }}                                    as is_sfha,
    occupancy_group,
    cause_group,
    flood_event_name,
    event_designation_number,
    nfip_community_id,
    policy_count,
    number_of_units,
    is_primary_residence,
    is_elevated,
    is_post_firm,
    is_rental,
    number_of_floors,
    basement_enclosure_type,
    construction_date_clean                                              as original_construction_date,
    case
        when construction_date_clean is not null and construction_date_clean <= date_of_loss
            then date_diff('year', construction_date_clean, date_of_loss)
    end                                                                  as building_age_at_loss_years,
    building_deductible_code,
    building_coverage,
    contents_coverage,
    case
        when building_coverage is null or building_coverage <= 0 then 'None / unknown'
        when building_coverage < 50000 then '< $50k'
        when building_coverage < 100000 then '$50k-100k'
        when building_coverage < 150000 then '$100k-150k'
        when building_coverage < 250000 then '$150k-250k'
        else '$250k (residential max) +'
    end                                                                  as building_coverage_band,
    water_depth_inches,
    flood_water_duration_hours,

    -- measures (nominal $)
    building_damage,
    contents_damage,
    building_paid,
    contents_paid,
    icc_paid,
    building_contents_paid,
    total_paid,
    -- measures (constant base-year $, CPI-U annual average)
    cpi_factor,
    total_paid * cpi_factor                                              as total_paid_real,
    building_contents_paid * cpi_factor                                  as building_contents_paid_real,

    -- claim status / cycle time
    total_paid > 0                                                       as is_paid_claim,
    non_payment_reason_building,
    case
        when most_recent_payment_date >= date_of_loss then date_diff('day', date_of_loss, most_recent_payment_date)
    end                                                                  as days_loss_to_last_payment,

    -- data-quality flags (documented in docs/DATA_QUALITY.md; tested with severity=warn)
    coalesce(building_paid < 0 or contents_paid < 0 or icc_paid < 0, false)              as dq_negative_payment,
    coalesce(building_paid > building_coverage * 1.01 and building_coverage > 0, false)  as dq_building_paid_exceeds_coverage,
    coalesce(construction_date_clean > date_of_loss, false)                              as dq_built_after_loss,
    coalesce(original_construction_date = date '1492-10-12', false)                       as dq_construction_date_sentinel,
    coalesce(claim_open_date < date_of_loss, false)                                      as dq_opened_before_loss,
    rated_flood_zone is null                                                             as dq_missing_flood_zone,
    county_fips_clean is null                                                            as dq_missing_county
from joined
