# Conversation Risk Analysis — Exec Summary

**Purpose:** identify which guest conversations are at risk of ending in a
cancellation/refund, and pinpoint where in the conversation we lose the
booking — as prep for the Evidence.dev report.

**Source data:** `int_booking_cancellation_risk`, `int_conversation_log`
(dbt models, `booking_project_pipeline/models/intermediate/`).

## Files

| File | Grain | Use |
|---|---|---|
| `conversation_journeys.csv` | 1 row per conversation | Full message-by-message journey: event sequence (`event_journey`), sentiment-label sequence, sentiment trajectory, opening/closing sentiment, negative-message/escalation/delay counts |
| `top10_cancellation_risk.csv` | 1 row per booking | The 10 highest cancellation-risk bookings, with the driving signals |
| `stage_impact_analysis.csv` | 1 row per conversation stage (opening/early-mid/late-mid/closing) | Sentiment, response latency, and risk correlation by stage |

## Data note: `intent_category` is conversation-level, not message-level

The source generator (`generate_hotel_chat_data.py`) assigns one `intent`
per conversation and copies it onto every message in that conversation.
So `fact_chat_messages.intent_category` carries no real per-message signal —
early versions of `conversation_journeys.csv` used it for `intent_journey`
and it just repeated the same value across the whole conversation.

Fixed in `int_conversation_log` (see `models/intermediate/int_conversation_log.sql`):
added an `event_label` per message derived from fields that *do* vary
message-to-message — `sender_type`, `sentiment_label`, a first-time
hand-off to `support_agent`, and a >15min reply-gap flag — e.g.
`opened_by_guest -> guest_neutral -> host_reply -> host_reply_delayed ->
guest_negative -> escalated_to_support`. This replaces `intent_journey`
with `event_journey` in the CSV; `conversation_primary_intent` is kept as a
conversation-level attribute (still useful for segmenting, just not for the
turn-by-turn sequence).

## Headline findings

### 1. Cancellation risk is identifiable, and it's concentrated

We score every confirmed booking 0–1 on a composite `cancellation_risk_score`
built from conversation signals: cancellation/refund intent, negative
sentiment, % negative messages, low CSAT, escalation, and response/resolution
speed. The riskiest bookings are unambiguous:

- All top-10 highest-risk bookings show `refund_request` intent, **escalated**
  status, CSAT of 1–2 out of 5, and 83–100% negative messages.
- Risk score for this group ranges **0.80–0.84** (vs. a possible max of 1.0),
  placing them at the very top of the risk distribution (percentile ≈ 0).

**Action:** these are not ambiguous "maybe" cases — they are conversations we
can flag and intervene on with high confidence today.

### 2. We lose the booking early, not late

We split every conversation into four sequential stages (opening → closing)
and asked: does the *stage* where negativity first shows up predict the
outcome?

| First negative message appears in... | Share of conversations | Avg. cancellation risk |
|---|---|---|
| Opening quarter | 21,180 | **0.352** |
| Early-mid | 8,438 | 0.308 |
| Late-mid | 3,384 | 0.284 |
| Closing quarter | 2,340 | 0.275 |

Risk drops **monotonically** the later negativity first appears. Conversations
that sour in the opening exchange carry ~28% more cancellation risk than ones
that only sour near the end.

This is compounded by response speed: average response latency roughly
**doubles right after the opening exchange** (185s → ~298s and holds there
through the rest of the conversation). The moment negativity is most likely to
appear is also the moment we're fastest to respond — but that speed advantage
disappears immediately after.

**Action:** the opening message is the highest-leverage intervention point.
Fast, sentiment-aware triage there — before response times slow down — is
where we have the best chance to change the outcome.

### 3. What's driving the risk

Across the top-risk segment, the dominant conversation intent is
**`refund_request`**, consistently paired with `escalated` status. This
matches the composite score design (cancellation/refund intent is the
single heaviest-weighted signal, 25%) and confirms it in the data: guests who
explicitly ask about refunds and get escalated are converting to cancellations
at far higher rates than the base population.

## Suggested Evidence.dev report structure

1. **KPI strip** — total bookings, % flagged top-risk-decile, avg risk score trend
2. **Risk leaderboard** — top-N at-risk bookings table (from `top10_cancellation_risk.csv`, extend to top 50–100 for the live report)
3. **Stage funnel chart** — cancellation risk by "stage negativity first appears" (from `stage_impact_analysis.csv` + the first-negative-position query)
4. **Response latency by stage** — bar chart showing the opening→post-opening latency jump
5. **Top concerns breakdown** — `intent_category` distribution within the at-risk segment (see `int_at_risk_conversation_concerns` model)
6. **Drill-down table** — `conversation_journeys.csv` filtered to a selected booking for support/CS review

## Caveats for exec framing

- Data is synthetic (portfolio project dataset), so findings validate the
  *approach*, not real business figures — frame as "this is the analysis we'd
  run on production data," not as live results.
- Risk score is rule-based/weighted, not a trained model — good for a first
  pass and interpretability, but call out that a future iteration could
  validate/replace weights with an actual model against realized cancellations.
