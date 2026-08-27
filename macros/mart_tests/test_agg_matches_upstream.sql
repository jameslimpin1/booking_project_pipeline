{% test agg_matches_upstream(model, compare_model, column_name=none, compare_column_name=none, compare_column_expr=none, model_agg='sum', compare_agg=none, model_where=none, compare_where=none, tolerance=1.0) %}

{#
    Reconciliation test: asserts an aggregate computed on `model` matches the
    same aggregate computed on `compare_model`, within `tolerance` (to absorb
    per-row rounding in mart SQL, e.g. round(sum(...), 2)). Use this whenever
    a mart re-groups or re-aggregates rows from an upstream model/source and
    the total should be unchanged, e.g. summing cancelled_price across every
    journey_stage in a month should reproduce that month's total from the
    upstream fact table.

    column_name / compare_column_name: ignored when the respective *_agg is
    'count' (count(*) is used instead). compare_column_expr takes a raw SQL
    expression instead of a plain column, for upstream models that don't
    store the metric as its own column (e.g. `case when is_cancelled then
    total_price else 0 end` in place of a `cancelled_price` column).
    model_where / compare_where are optional raw SQL filters, for
    reconciling against a filtered subset of the upstream model (e.g. a
    specific event_pattern).
#}

{% set compare_column = compare_column_expr or compare_column_name or column_name %}
{% set compare_agg_fn = compare_agg or model_agg %}

{% set model_expr = 'count(*)' if model_agg == 'count' else model_agg ~ '(' ~ column_name ~ ')' %}
{% set compare_expr = 'count(*)' if compare_agg_fn == 'count' else compare_agg_fn ~ '(' ~ compare_column ~ ')' %}

with model_side as (

    select {{ model_expr }} as agg_value
    from {{ model }}
    {% if model_where %}where {{ model_where }}{% endif %}

),

compare_side as (

    select {{ compare_expr }} as agg_value
    from {{ compare_model }}
    {% if compare_where %}where {{ compare_where }}{% endif %}

)

select
    model_side.agg_value as model_value,
    compare_side.agg_value as compare_value,
    abs(coalesce(model_side.agg_value, 0) - coalesce(compare_side.agg_value, 0)) as diff
from model_side
cross join compare_side
where abs(coalesce(model_side.agg_value, 0) - coalesce(compare_side.agg_value, 0)) > {{ tolerance }}

{% endtest %}
