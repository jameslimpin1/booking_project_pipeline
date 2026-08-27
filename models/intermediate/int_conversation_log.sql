{{ config(
    materialized = 'table',
    tags = ['intermediate']
) }}

with conversations as (

    select
        conversation_id,
        booking_id,
        user_id,
        host_id,
        property_id,
        channel as conversation_channel,
        status as conversation_status,
        csat_score
    from {{ ref('stg_dim_conversations') }}

),

messages as (

    select *
    from {{ ref('stg_fact_chat_messages') }}

),

sequenced as (

    select
        m.message_id,
        m.conversation_id,
        c.booking_id,
        c.user_id,
        c.host_id,
        c.property_id,
        c.conversation_channel,
        c.conversation_status,
        c.csat_score,
        m.sender_type,
        m.sender_id,
        m.sent_at,
        m.text,
        m.intent_category,
        m.sentiment_score,
        m.sentiment_label,
        m.response_latency_seconds,
        row_number() over (partition by m.conversation_id order by m.sent_at, m.message_id) as message_seq,
        lag(m.sent_at) over (partition by m.conversation_id order by m.sent_at, m.message_id) as prev_message_sent_at,
        lag(m.sender_type) over (partition by m.conversation_id order by m.sent_at, m.message_id) as prev_sender_type,
        count(*) over (partition by m.conversation_id) as conversation_message_count
    from messages m
    inner join conversations c using (conversation_id)

),

flagged as (

    select
        *,
        message_seq = 1 as is_first_message,
        message_seq = conversation_message_count as is_last_message,
        datediff('second', prev_message_sent_at, sent_at) as seconds_since_prev_message,
        -- note: intent_category is assigned once per conversation in the source
        -- data and copied onto every message, so it carries no message-level
        -- signal on its own. sender_type + sentiment_label do vary per message
        -- and are the basis for event_label below.
        sender_type = 'support_agent' and coalesce(prev_sender_type, '') != 'support_agent' as is_escalation_handoff
    from sequenced

)

select
    *,
    -- proxy event label: what actually happened at this turn, derived from
    -- sender role + sentiment + handoff/delay markers (all message-grain
    -- fields), used in place of the conversation-level intent_category for
    -- journey/event-sequence analysis.
    case
        when is_first_message then 'opened_by_' || sender_type
        when is_escalation_handoff then 'escalated_to_support'
        when sender_type = 'guest' then 'guest_' || sentiment_label
        when sender_type = 'bot' then 'bot_reply'
        when sender_type = 'host' then 'host_reply'
        when sender_type = 'support_agent' then 'support_reply'
    end
        || case when not is_first_message and seconds_since_prev_message > 900 then '_delayed' else '' end
        as event_label
from flagged
