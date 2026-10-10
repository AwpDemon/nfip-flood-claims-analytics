{#
  Calendar dimension (one row per day). The spine covers every date of loss in the data
  and every exposure year. Atlantic hurricane season = June 1 - November 30.
#}
with bounds as (
    select
        least(min(date_of_loss), date '{{ var('exposure_start_year') - 1 }}-01-01') as start_date,
        greatest(max(date_of_loss), date '{{ var('exposure_end_year') + 1 }}-12-31') as end_date
    from {{ ref('int_claims_enriched') }}
),

spine as (
    select cast(unnest(generate_series(
        cast(date_trunc('year', start_date) as date),
        cast(date_trunc('year', end_date) + interval 1 year - interval 1 day as date),
        interval 1 day
    )) as date) as date_day
    from bounds
)

select
    date_day                                         as date_key,
    date_day,
    year(date_day)                                   as calendar_year,
    quarter(date_day)                                as calendar_quarter,
    month(date_day)                                  as month_number,
    strftime(date_day, '%b')                         as month_name,
    cast(date_trunc('month', date_day) as date)      as month_start,
    dayofweek(date_day)                              as day_of_week,
    (year(date_day) // 10) * 10                      as decade,
    month(date_day) between 6 and 11                 as is_atlantic_hurricane_season
from spine
