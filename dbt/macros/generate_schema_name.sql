{# Use the custom schema name verbatim (staging, marts, ...) instead of dbt's default
   "<target_schema>_<custom>" concatenation, so BI tools see clean schema names. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}{{ target.schema }}{%- else -%}{{ custom_schema_name | trim }}{%- endif -%}
{%- endmacro %}
