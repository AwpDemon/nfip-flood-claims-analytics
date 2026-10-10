{#
  Map a FEMA rated flood zone code to analysis groups.

  FIRM zone families (OpenFEMA data dictionary):
    V, VE, V1-V30                              coastal high hazard (wave action)  -> SFHA
    A, AE, A1-A30, AH, AO, AHB, AOB, A99, AR*  1%-annual-chance flood             -> SFHA
    X, B, C                                    moderate / minimal (outside SFHA)
    D                                          undetermined
#}
{% macro flood_zone_group(col) -%}
    case
        when {{ col }} is null or trim({{ col }}) = '' then 'Unknown'
        when upper(trim({{ col }})) like 'V%' then 'V (coastal high hazard)'
        when upper(trim({{ col }})) like 'A%' then 'A (1% annual chance)'
        when upper(trim({{ col }})) in ('X', 'B', 'C') then 'X/B/C (moderate-minimal)'
        when upper(trim({{ col }})) = 'D' then 'D (undetermined)'
        else 'Unknown'
    end
{%- endmacro %}

{% macro flood_zone_detail(col) -%}
    case
        when {{ col }} is null or trim({{ col }}) = '' then 'Unknown'
        when upper(trim({{ col }})) = 'V' then 'V (no BFE)'
        when regexp_full_match(upper(trim({{ col }})), 'V(E|[0-9]{1,2})') then 'VE / V1-V30 (BFE)'
        when upper(trim({{ col }})) = 'A' then 'A (no BFE)'
        when upper(trim({{ col }})) = 'A99' or upper(trim({{ col }})) like 'AR%' then 'A99/AR (protected/restoring)'
        when regexp_full_match(upper(trim({{ col }})), 'A(E|[0-9]{1,2})') then 'AE / A1-A30 (BFE)'
        when upper(trim({{ col }})) in ('AH', 'AHB', 'AO', 'AOB') then 'AH/AO (shallow)'
        when upper(trim({{ col }})) = 'X' then 'X'
        when upper(trim({{ col }})) = 'B' then 'B (moderate, pre-1986 map)'
        when upper(trim({{ col }})) = 'C' then 'C (minimal, pre-1986 map)'
        when upper(trim({{ col }})) = 'D' then 'D (undetermined)'
        else 'Other / unrecognized'
    end
{%- endmacro %}

{% macro is_sfha(col) -%}
    coalesce(upper(trim({{ col }})) like 'A%' or upper(trim({{ col }})) like 'V%', false)
{%- endmacro %}
