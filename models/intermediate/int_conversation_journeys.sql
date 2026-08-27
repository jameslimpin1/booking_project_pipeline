{{ config(
    materialized = 'table',
    tags = ['intermediate']
) }}

-- One row per conversation, built by collapsing int_conversation_log's
-- message-grain event log up to conversation grain. This is the shared base
-- for every "loss point" / journey analysis in the marts layer (event
-- sequence, sentiment arc, escalation/delay counts) — build new
-- conversation-level metrics here rather than re-deriving them downstream.

with log as (

    select *
    from {{ ref('int_conversation_log') }}

),

aggregated as (

    select
        conversation_id,
        booking_id,
        user_id,
        host_id,
        property_id,
        any_value(conversation_channel) as conversation_channel,
        any_value(conversation_status) as conversation_status,
        any_value(csat_score) as csat_score,
        -- conversation-level topic label. Note: this is copied onto every
        -- message by the source data (see int_conversation_log), so any_value
        -- is equivalent to a group-by column here, not a lossy aggregation.
        any_value(intent_category) as conversation_primary_intent,
        max(conversation_message_count) as conversation_message_count,
        min(sent_at) as conversation_started_at,
        max(sent_at) as conversation_ended_at,
        datediff('second', min(sent_at), max(sent_at)) as duration_seconds,
        -- the message-by-message story of the conversation, in order.
        string_agg(event_label, ' -> ' order by message_seq) as event_journey,
        string_agg(sentiment_label, ' -> ' order by message_seq) as sentiment_label_journey,
        avg(sentiment_score) as avg_sentiment_score,
        max(case when is_first_message then sentiment_score end) as opening_sentiment,
        max(case when is_last_message then sentiment_score end) as closing_sentiment,
        sum(case when sentiment_label = 'negative' then 1 else 0 end) as negative_msg_count,
        sum(is_escalation_handoff::int) as escalation_handoff_count,
        sum(case when not is_first_message and seconds_since_prev_message > 900 then 1 else 0 end)
            as delayed_reply_count,
        max(case when intent_category in ('cancellation_request', 'refund_request') then 1 else 0 end)
            as raised_cancellation_or_refund,
        avg(response_latency_seconds) as avg_response_latency_seconds
    from log
    group by conversation_id, booking_id, user_id, host_id, property_id

)

select
    *,
    closing_sentiment - opening_sentiment as sentiment_delta
from aggregated
