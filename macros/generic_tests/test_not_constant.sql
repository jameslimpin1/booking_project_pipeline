{% test not_constant(model, column_name) %}

-- Catches a model silently collapsing to one value (e.g. a broken join or
-- CASE expression) that unique/not_null wouldn't flag on their own.
select count(distinct {{ column_name }}) as distinct_value_count
from {{ model }}
having count(distinct {{ column_name }}) <= 1

{% endtest %}
