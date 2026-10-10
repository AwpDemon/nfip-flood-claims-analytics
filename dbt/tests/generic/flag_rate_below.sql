{% test flag_rate_below(model, column_name, max_rate) %}
{#-
  Thresholded data-quality test for boolean flag columns.

  Real-world data always has *some* bad rows; a test that fails on the first one is noise, and
  one that never fails is useless. This test returns one row (= failure) when the share of rows
  where the flag is true exceeds `max_rate`, so it fires on a *regression* in source quality,
  not on the known, documented baseline.
-#}
with stats as (
    select
        avg(case when {{ column_name }} then 1.0 else 0.0 end) as flag_rate,
        count(*) as n_rows
    from {{ model }}
)
select *
from stats
where flag_rate > {{ max_rate }}
{% endtest %}
