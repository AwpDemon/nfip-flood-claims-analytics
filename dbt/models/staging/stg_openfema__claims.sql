{#
  One row per NFIP claim, typed and renamed.

  Two physical sources feed this model:
    * the bulk Parquet (full history, refreshed by FEMA roughly monthly), and
    * API increments for recent loss years (claims still developing).
  A claim can appear in both; we keep the newest snapshot, preferring the API copy on ties
  because it is pulled after the bulk file.
#}
with bulk as (
    select {{ claims_columns('bulk') }}
    from {{ source('openfema', 'nfip_claims_bulk') }}
),

api as (
    select {{ claims_columns('api') }}
    from {{ source('openfema', 'nfip_claims_api_increments') }}
),

unioned as (
    select * from bulk
    union all
    select * from api
),

deduped as (
    select *
    from unioned
    qualify row_number() over (
        partition by claim_id
        order by source_as_of_ts desc nulls last, (record_source = 'api') desc
    ) = 1
)

select
    *,
    coalesce(building_paid, 0) + coalesce(contents_paid, 0)                         as building_contents_paid,
    coalesce(building_paid, 0) + coalesce(contents_paid, 0) + coalesce(icc_paid, 0) as total_paid
from deduped
