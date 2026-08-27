{% test value_matches_upstream(model, column_name, compare_model, join_key, compare_column_name=none) %}

{#
    Row-level reconciliation test: for every row in `model`, the value in
    `column_name` must exactly match `compare_column_name` on the upstream
    row with the same `join_key` (e.g. a mart carrying total_price onto every
    conversation row should always match the source booking's total_price,
    even though the mart's grain is conversation, not booking).
#}

{% set compare_column = compare_column_name or column_name %}

select
    m.{{ join_key }},
    m.{{ column_name }} as model_value,
    c.{{ compare_column }} as upstream_value
from {{ model }} m
inner join {{ compare_model }} c on m.{{ join_key }} = c.{{ join_key }}
where m.{{ column_name }} != c.{{ compare_column }}
   or (m.{{ column_name }} is null) != (c.{{ compare_column }} is null)

{% endtest %}
