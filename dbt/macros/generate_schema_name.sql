{#
  Use the configured schema name as-is (silver, gold, ref) instead of dbt's
  default "<target_schema>_<custom_schema>". Environments are isolated by
  catalog (hc_dev / hc_qa / hc_prod), so schema names stay identical everywhere.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}{{ target.schema }}{%- else -%}{{ custom_schema_name | trim }}{%- endif -%}
{%- endmacro %}
