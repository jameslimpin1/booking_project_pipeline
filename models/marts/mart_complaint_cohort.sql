{{ config(
    materialized = 'table',
    tags = ['marts']
) }}

-- Powers the dashboard's "Top 5 complaints" / "Top 5 everyday questions" and
-- the "which complaints hurt each cohort most" tier breakdown. Grain: one
-- row per (month, channel, loyalty_tier, intent_category). topic_cluster
-- groups intent_category into complaint / everyday / praise buckets (see
-- mart_booking_loss_events for the exact intent -> cluster mapping).

select
    activity_month as month,
    conversation_channel as channel,
    loyalty_tier,
    conversation_primary_intent as intent_category,
    topic_cluster,
    count(*) as bookings,
    sum(is_cancelled::int) as cancelled_bookings,
    round(sum(total_price), 2) as total_price,
    round(sum(case when is_cancelled then total_price else 0 end), 2) as cancelled_price
from {{ ref('mart_booking_loss_events') }}
group by 1, 2, 3, 4, 5
