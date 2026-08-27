{{ config(
    materialized = 'table',
    tags = ['marts']
) }}

-- Powers the dashboard's month x event-pattern impact heatmap. Grain: one
-- row per (month, channel, loyalty_tier, event_pattern). Because
-- event_pattern in mart_booking_loss_events is mutually exclusive, summing
-- cancelled_price for a given month across every event_pattern reproduces
-- that month's true total revenue lost — no double-counting.

select
    activity_month as month,
    conversation_channel as channel,
    loyalty_tier,
    event_pattern,
    count(*) as bookings,
    sum(is_cancelled::int) as cancelled_bookings,
    round(sum(case when is_cancelled then total_price else 0 end), 2) as cancelled_price
from {{ ref('mart_booking_loss_events') }}
group by 1, 2, 3, 4
