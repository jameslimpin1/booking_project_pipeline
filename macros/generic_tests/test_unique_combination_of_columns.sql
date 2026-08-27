{% test unique_combination_of_columns(model, combination_of_columns) %}

with grouped as (

    select
        {{ combination_of_columns | join(', ') }},
        count(*) as row_count
    from {{ model }}
    group by {{ combination_of_columns | join(', ') }}

)

select *
from grouped
where row_count > 1

{% endtest %}
