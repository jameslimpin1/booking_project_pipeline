{{ config(
    materialized = 'table',
    tags = ['marts']
) }}

-- Powers the dashboard's "which part of the journey do we usually lose"
-- table and the revenue-lost hero stats (both are this table filtered/
-- resummed by the page's month/channel/tier/stage slicers). Grain: one row
-- per (month, channel, loyalty_tier, journey_stage). total_price is kept
-- alongside cancelled_price so the page can also show "% of value lost".

select
    activity_month as month,
    conversation_channel as channel,
    loyalty_tier,
    journey_stage,
    count(*) as bookings,
    sum(is_cancelled::int) as cancelled_bookings,
    round(sum(total_price), 2) as total_price,
    round(sum(case when is_cancelled then total_price else 0 end), 2) as cancelled_price
from {{ ref('mart_booking_loss_events') }}
group by 1, 2, 3, 4
