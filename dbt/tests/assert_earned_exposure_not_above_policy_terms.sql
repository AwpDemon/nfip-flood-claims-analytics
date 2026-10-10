-- Earned exposure in a calendar year cannot exceed one policy-year per policy term active in that
-- year. Checked nationally per year against the staging view: earned years <= distinct terms
-- overlapping the year. A failure means the pro-rata day arithmetic double counts.
with active as (
    select
        y.calendar_year,
        count(*) as terms_active
    from {{ ref('stg_openfema__policies') }} as p
    join (select unnest(generate_series({{ var('exposure_start_year') }}, {{ var('exposure_end_year') }})) as calendar_year) as y
        on p.policy_effective_date < make_date(y.calendar_year + 1, 1, 1)
       and p.term_end_date > make_date(y.calendar_year, 1, 1)
    where p.term_end_date > p.policy_effective_date
    group by 1
),
earned as (
    select calendar_year, sum(earned_policy_years) as earned_policy_years
    from {{ ref('fact_policy_exposure') }}
    where is_complete_exposure_year
    group by 1
)
select e.calendar_year, e.earned_policy_years, a.terms_active
from earned as e
join active as a using (calendar_year)
where e.earned_policy_years > a.terms_active * 1.0001
