{{ config(
    materialized = 'table',
    tags = ['marts']
) }}

-- Powers the dashboard's "booking value doesn't predict who cancels, but it
-- predicts what it costs us" chart. Splits the trailing-12-month population
-- into 5 equal-sized groups by total_price and compares cancellation rate
-- (flat across groups) against revenue_lost_to_cancellation (concentrated
-- in the top group) — the two tell different stories on purpose.

with events as (

    select *
    from {{ ref('mart_booking_loss_events') }}

),

quintiled as (

    select
        *,
        ntile(5) over (order by total_price) as price_quintile
    from events

)

select
    price_quintile,
    count(*) as bookings,
    round(avg(total_price), 2) as avg_price,
    round(min(total_price), 2) as min_price,
    round(max(total_price), 2) as max_price,
    sum(is_cancelled::int) as cancelled_bookings,
    round(100.0 * sum(is_cancelled::int) / count(*), 2) as cancellation_rate_pct,
    round(sum(case when is_cancelled then total_price else 0 end), 2) as revenue_lost_to_cancellation
from quintiled
group by 1
order by 1
