{{ config(
    materialized = 'table',
    tags = ['marts']
) }}

-- Master fact table for every "why do we lose bookings" analysis on the
-- dashboard: one row per conversation in the trailing 12 months, carrying
-- the booking's price/outcome, the guest's loyalty tier, and two derived
-- classifications used throughout the marts layer:
--   - journey_stage: WHERE in the conversation things went wrong
--   - event_pattern: WHAT happened, as a single mutually-exclusive category
--
-- Grain note: this is CONVERSATION grain, not booking grain. A booking with
-- two conversations in the window contributes two rows, each carrying that
-- booking's full price/outcome. This matches how the dashboard's "bookings"
-- figures have always been computed (conversation count, not distinct
-- booking count) — do not dedupe to booking_id without checking every
-- downstream aggregate that assumes this grain.

with journeys as (

    select *
    from {{ ref('int_conversation_journeys') }}

),

window_bound as (

    -- trailing 12-month window, anchored to the most recent conversation in
    -- the data rather than a wall-clock date, so the mart stays reproducible
    -- as new synthetic data is regenerated.
    select max(conversation_started_at) as max_started_at
    from journeys

),

bookings as (

    select
        booking_id,
        status as booking_status,
        total_price
    from {{ ref('stg_fact_bookings') }}

),

users as (

    select
        user_id,
        coalesce(loyalty_tier, 'none') as loyalty_tier
    from {{ ref('stg_dim_users') }}

),

scoped as (

    select
        j.*,
        b.booking_status,
        b.total_price,
        u.loyalty_tier,
        strftime(j.conversation_started_at, '%Y-%m') as activity_month,
        b.booking_status = 'cancelled' as is_cancelled
    from journeys j
    inner join bookings b on b.booking_id = j.booking_id
    inner join users u on u.user_id = j.user_id
    cross join window_bound w
    where j.conversation_started_at >= w.max_started_at - interval '12' month

),

flagged as (

    select
        *,
        -- WHERE: which stretch of the conversation the negativity shows up
        -- in, used by the journey-stage breakdown.
        case
            when negative_msg_count = 0 then '4_closing'
            when opening_sentiment < -0.2 then '1_opening'
            when sentiment_delta < -0.3 then '3_late_mid'
            else '2_early_mid'
        end as journey_stage,
        -- topic grouping for the complaint/everyday-question cohort views.
        case
            when conversation_primary_intent in (
                'noise_complaint', 'checkin_issue', 'amenity_complaint', 'refund_request',
                'payment_issue', 'cleanliness_complaint', 'host_unresponsive'
            ) then 'complaint'
            when conversation_primary_intent = 'positive_feedback' then 'praise'
            else 'everyday'
        end as topic_cluster,
        -- WHAT: the individual boolean signals event_pattern below picks
        -- between. Kept as named flags (rather than inlined) so the
        -- priority order is easy to read and re-order.
        event_journey = 'opened_by_guest' as flag_unanswered_opening,
        closing_sentiment < -0.2 as flag_ends_frustrated,
        delayed_reply_count > 0 as flag_delayed_reply,
        escalation_handoff_count > 0 as flag_escalated_support,
        opening_sentiment < -0.2 as flag_negative_opening,
        negative_msg_count >= 2 as flag_multiple_negative,
        escalation_handoff_count > 0 and delayed_reply_count > 0 as flag_escalated_and_delayed,
        sentiment_delta < -0.3 as flag_sentiment_worsened,
        conversation_message_count > 10 as flag_long_conversation,
        csat_score <= 2 as flag_low_csat
    from scoped

)

select
    *,
    -- WHAT: each conversation lands in exactly ONE row here, by priority
    -- (first match wins). This mirrors the dashboard's "impact heatmap":
    -- summing cancelled_price across event_pattern for a given month
    -- reproduces that month's true revenue lost with no double-counting,
    -- because every conversation is counted once. 'no_pattern' captures
    -- cancellations that don't match any of the 10 named patterns, so the
    -- category is exhaustive.
    case
        when flag_unanswered_opening then 'unanswered_opening'
        when flag_escalated_and_delayed then 'escalated_and_delayed'
        when flag_escalated_support then 'escalated_support'
        when flag_delayed_reply then 'delayed_reply'
        when flag_ends_frustrated then 'ends_frustrated'
        when flag_negative_opening then 'negative_opening'
        when flag_multiple_negative then 'multiple_negative'
        when flag_sentiment_worsened then 'sentiment_worsened'
        when flag_long_conversation then 'long_conversation'
        when flag_low_csat then 'low_csat'
        else 'no_pattern'
    end as event_pattern
from flagged
