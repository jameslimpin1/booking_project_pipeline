{{ config(
    materialized = 'table',
    tags = ['intermediate']
) }}

with bookings as (

    select *
    from {{ ref('stg_fact_bookings') }}
    where status = 'confirmed'

),

conversations as (

    select *
    from {{ ref('stg_dim_conversations') }}
    where booking_id is not null

),

messages as (

    select *
    from {{ ref('stg_fact_chat_messages') }}

),

message_agg as (

    select
        conversation_id,
        avg(sentiment_score) as avg_sentiment_score,
        sum(case when sentiment_label = 'negative' then 1 else 0 end)::float
            / nullif(count(*), 0) as pct_negative_messages,
        avg(response_latency_seconds) as avg_msg_response_latency_seconds
    from messages
    group by 1

),

conversation_agg as (

    select
        c.booking_id,
        count(*) as conversation_count,
        max(case when c.intent_category in ('cancellation_request', 'refund_request') then 1 else 0 end)
            as has_cancellation_or_refund_intent,
        max(case when c.status = 'escalated' then 1 else 0 end) as has_escalated_conversation,
        avg(c.csat_score) as avg_csat_score,
        avg(c.first_response_time_seconds) as avg_first_response_time_seconds,
        avg(c.resolution_time_seconds) as avg_resolution_time_seconds,
        avg(m.avg_sentiment_score) as avg_sentiment_score,
        avg(m.pct_negative_messages) as pct_negative_messages,
        avg(m.avg_msg_response_latency_seconds) as avg_msg_response_latency_seconds
    from conversations c
    left join message_agg m using (conversation_id)
    group by 1

),

joined as (

    select
        b.booking_id,
        b.user_id,
        b.property_id,
        b.host_id,
        b.checkin_date,
        b.checkout_date,
        b.total_price,
        coalesce(ca.conversation_count, 0) as conversation_count,
        coalesce(ca.has_cancellation_or_refund_intent, 0) as has_cancellation_or_refund_intent,
        coalesce(ca.has_escalated_conversation, 0) as has_escalated_conversation,
        ca.avg_csat_score,
        ca.avg_first_response_time_seconds,
        ca.avg_resolution_time_seconds,
        ca.avg_sentiment_score,
        ca.pct_negative_messages,
        ca.avg_msg_response_latency_seconds
    from bookings b
    left join conversation_agg ca using (booking_id)

),

normalized as (

    select
        *,
        -- each raw signal normalized to 0-1; bookings with no conversations get
        -- neutral (0) contribution on conversation-derived signals, not penalized
        has_cancellation_or_refund_intent::float as sig_cancel_intent,
        has_escalated_conversation::float as sig_escalated,
        coalesce(1 - (avg_sentiment_score + 1) / 2, 0) as sig_negative_sentiment,
        coalesce(pct_negative_messages, 0) as sig_pct_negative_msgs,
        coalesce((5 - avg_csat_score) / 4.0, 0) as sig_low_csat,
        coalesce(percent_rank() over (order by avg_first_response_time_seconds), 0) as sig_slow_first_response,
        coalesce(percent_rank() over (order by avg_resolution_time_seconds), 0) as sig_slow_resolution,
        coalesce(percent_rank() over (order by avg_msg_response_latency_seconds), 0) as sig_slow_msg_latency
    from joined

),

scored as (

    select
        *,
        (
            0.25 * sig_cancel_intent
            + 0.20 * sig_negative_sentiment
            + 0.15 * sig_pct_negative_msgs
            + 0.15 * sig_low_csat
            + 0.10 * sig_escalated
            + 0.05 * sig_slow_first_response
            + 0.05 * sig_slow_resolution
            + 0.05 * sig_slow_msg_latency
        ) as cancellation_risk_score
    from normalized

),

ranked as (

    select
        *,
        percent_rank() over (order by cancellation_risk_score desc) as risk_percentile
    from scored

)

select
    *,
    risk_percentile <= 0.10 as is_top_risk_decile
from ranked
