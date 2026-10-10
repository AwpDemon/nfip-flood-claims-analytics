{% test non_negative(model, column_name, where_clause=none) %}
{#- Fails for every row where the column is negative (NULLs are ignored). -#}
select *
from {{ model }}
where {{ column_name }} < 0
{% if where_clause %}and ({{ where_clause }}){% endif %}
{% endtest %}
