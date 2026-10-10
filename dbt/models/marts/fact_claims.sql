{{
    config(
        materialized = 'incremental',
        unique_key = 'claim_id',
        incremental_strategy = 'delete+insert',
        on_schema_change = 'fail'
    )
}}
{#
  Claim-grain fact (one row per NFIP claim).

  Incremental strategy mirrors the extract: claims keep developing for a few years after the
  loss, so each incremental run deletes and re-inserts every claim whose loss year falls in the
  rolling window [max loss year - lookback, max loss year]. Older loss years are treated as
  settled. `dbt build --full-refresh` rebuilds everything.
#}
select
    -- keys
    claim_id,
    date_key,
    geography_key,
    flood_zone_key,
    event_key,
    occupancy_key,
    cause_key,

    -- degenerate dimensions / claim attributes
    loss_year,
    event_designation_number,
    nfip_community_id,
    record_source,
    policy_count,
    number_of_units,
    is_primary_residence,
    is_elevated,
    is_post_firm,
    is_rental,
    number_of_floors,
    basement_enclosure_type,
    building_age_at_loss_years,
    building_deductible_code,
    building_coverage_band,
    water_depth_inches,
    flood_water_duration_hours,
    is_paid_claim,
    non_payment_reason_building,
    days_loss_to_last_payment,

    -- measures, nominal dollars
    building_coverage,
    contents_coverage,
    building_damage,
    contents_damage,
    building_paid,
    contents_paid,
    icc_paid,
    building_contents_paid,
    total_paid,

    -- measures, constant {{ var('cpi_base_year') }} dollars
    cpi_factor,
    total_paid_real,
    building_contents_paid_real,

    -- data-quality flags
    dq_negative_payment,
    dq_building_paid_exceeds_coverage,
    dq_built_after_loss,
    dq_construction_date_sentinel,
    dq_opened_before_loss,
    dq_missing_flood_zone,
    dq_missing_county
from {{ ref('int_claims_enriched') }}
{% if is_incremental() %}
where loss_year >= (select max(loss_year) from {{ this }}) - {{ var('incremental_lookback_years') }}
{% endif %}
