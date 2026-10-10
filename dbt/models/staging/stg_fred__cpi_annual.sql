{#
  Annual-average CPI-U (BLS series CUSR0000SA0 via FRED CPIAUCSL) and the factor that converts
  a nominal dollar in year t into constant {{ var('cpi_base_year') }} dollars.
  A year needs at least 11 of 12 monthly observations. This is not hypothetical: BLS did not
  publish an October 2025 CPI (federal government shutdown), so FRED has a blank for 2025-10 and
  a strict 12-month rule would silently drop the base year and null every real-dollar measure.
  `months_observed` is kept so the gap is visible downstream (tested in schema.yml).
#}
with monthly as (
    select
        cast(observation_date as date) as month_start,
        cast(CPIAUCSL as double)       as cpi_u
    from {{ source('fred', 'cpi_u_monthly') }}
    where CPIAUCSL is not null
),

annual as (
    select
        year(month_start)  as calendar_year,
        avg(cpi_u)         as cpi_u_annual_avg,
        count(*)           as months_observed
    from monthly
    group by 1
    having count(*) >= 11
)

select
    calendar_year,
    cpi_u_annual_avg,
    months_observed,
    (select cpi_u_annual_avg from annual where calendar_year = {{ var('cpi_base_year') }})
        / cpi_u_annual_avg as to_base_year_factor,
    {{ var('cpi_base_year') }} as base_year
from annual
