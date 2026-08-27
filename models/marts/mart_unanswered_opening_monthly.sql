{{ config(
    materialized = 'table',
    tags = ['marts']
) }}

-- Monthly cohort view of the "unanswered opening message" pattern, the
-- single strongest driver found in the loss analysis (1.58x baseline). Kept
-- as its own model because the month-over-month rate is genuinely noisy at
-- this sample size (as few as 0-11 cases/month) — see the note on
-- unanswered_opening_bookings in the schema doc before treating any single
-- month's rate as a trend.

with events as (

    select *
    from {{ ref('mart_booking_loss_events') }}

),

monthly_base as (

    select
        activity_month as month,
        count(*) as total_bookings_in_cohort,
        round(100.0 * sum(is_cancelled::int) / count(*), 4) as cohort_base_cancel_rate_pct
    from events
    group by 1

),

monthly_unanswered as (

    select
        activity_month as month,
        count(*) as unanswered_opening_bookings,
        sum(is_cancelled::int) as unanswered_opening_cancelled
    from events
    where event_pattern = 'unanswered_opening'
    group by 1

)

select
    b.month,
    b.total_bookings_in_cohort,
    b.cohort_base_cancel_rate_pct,
    coalesce(u.unanswered_opening_bookings, 0) as unanswered_opening_bookings,
    round(100.0 * coalesce(u.unanswered_opening_bookings, 0) / b.total_bookings_in_cohort, 4)
        as pct_unanswered_opening,
    coalesce(u.unanswered_opening_cancelled, 0) as unanswered_opening_cancelled,
    round(
        100.0 * coalesce(u.unanswered_opening_cancelled, 0) / nullif(u.unanswered_opening_bookings, 0), 4
    ) as unanswered_opening_cancel_rate_pct
from monthly_base b
left join monthly_unanswered u using (month)
order by b.month
