{% test unique_combination(model, columns) %}
{#- Grain test without a package dependency: no two rows share the same combination of `columns`. -#}
select {{ columns | join(', ') }}, count(*) as n
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}
