{{ config(
    materialized = 'table',
    tags = ['intermediate']
) }}

with at_risk_conversations as (

    select *
    from {{ ref('int_at_risk_conversations') }}

),

concern_counts as (

    select
        intent_category,
        count(*) as conversation_count,
        count(*)::float / sum(count(*)) over () as pct_of_at_risk_conversations
    from at_risk_conversations
    group by 1

)

select
    intent_category,
    conversation_count,
    pct_of_at_risk_conversations,
    rank() over (order by conversation_count desc) as concern_rank
from concern_counts
qualify concern_rank <= 5
order by concern_rank
