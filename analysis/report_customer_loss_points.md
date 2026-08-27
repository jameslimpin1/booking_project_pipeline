# Why We Lose Bookings — and What Would Prevent It

**TL;DR:** Roughly **1 in 7 bookings gets cancelled**. What the guest is
messaging *about* barely matters — complaint threads cancel at about the same
rate as ordinary questions. What matters is whether we **reply at all**.
Guests whose opening message goes completely unanswered cancel at nearly
**1 in 4** — well above average. That's the single clearest, fixable driver
we found.

**Scope:** all bookings with a conversation in the last 12 months of data
(2024-07-22 to 2025-07-22), 58,505 bookings total.

---

## 1. The baseline: how often do we actually lose the booking?

- **1 out of 7 bookings (14.8%)** ends in cancellation.
- That's the number every finding below is measured against — a pattern only
  matters if it pushes meaningfully above or below that 1-in-7 rate.

## 2. What guests are messaging about doesn't move the needle much

We grouped every conversation by topic into three buckets and checked the
cancellation rate for each:

| Topic group | Example topics | Share of conversations | Cancellation rate |
|---|---|---|---|
| Complaints | noise, cleanliness, amenities, refunds, payment issues, unresponsive host | 1 in 3 | 1 in 7 (14.5%) |
| Everyday questions | booking questions, check-in info, wifi, extending a stay | 3 in 5 | 1 in 7 (15.0%) |
| Compliments | positive feedback | 1 in 24 | 1 in 7 (14.0%) |

**Takeaway: a guest complaining is no more likely to cancel than a guest
asking a routine question.** The topic of the conversation is not the risk
signal — this rules out "guests who complain are the ones we lose" as an
explanation, and points us toward *how the conversation is handled* instead
of *what it's about*.

## 3. The one pattern that clearly predicts cancellation: silence

We checked several ways a conversation could go badly — the guest ending on
a frustrated note, a slow reply somewhere in the thread, escalating to a
support agent — and lined each up against the 1-in-7 baseline:

| What happened in the conversation | How often it happens | Cancellation rate | Vs. the 1-in-7 baseline |
|---|---|---|---|
| **Guest's opening message never gets a reply** | 1 in 680 bookings | **Nearly 1 in 4 (23.3%)** | **58% higher** |
| Conversation ends on a frustrated note from the guest | Just under half of bookings | 1 in 7 (14.9%) | No real difference |
| A reply anywhere in the thread takes over 15 minutes | About 1 in 3 bookings | 1 in 7 (14.8%) | No real difference |
| Conversation gets escalated to a support agent | About half of bookings | 1 in 7 (14.6%) | No real difference |

Only one pattern stands out: **conversations that go completely unanswered.**
Everything else we tested — ending on a bad note, a slow reply, escalating —
lands right on the baseline. Guests don't seem to punish us for a rocky
conversation as long as they get *a* reply. They punish silence.

**In plain terms: if we simply respond to every guest's opening message, we
address the clearest cancellation risk we can currently see in the data.**

## 4. Is there a time or staffing angle — a quarter or weekday we should focus on?

Short answer: **no.** We checked inquiry volume and cancellation rate by
quarter, by month, by day of week, and by hour of day. Volume is essentially
flat across all of them (no quarter or weekday brings meaningfully more
inquiries than another), and the cancellation rate stays inside a **tight
13-16% band everywhere** we cut it. There's no seasonal spike, no bad weekday,
no bad hour. Timing is not a lever here — the fix is structural (answer every
opening message), not a staffing schedule change.

We also looked at whether unanswered-opening-message cancellations are
getting better or worse over time. Right now: **not reliably** — this pattern
is rare enough (roughly 2-11 cases per month) that a month-to-month view
bounces between 0% and ~37% purely from small numbers, not a real trend. The
overall 12-month figure — nearly 1 in 4 — is solid; the month-by-month
breakdown isn't, yet.

## 5. Booking value doesn't change *who* cancels — but it changes what it costs us

We split all 72,348 bookings into five equal-sized groups by price, from
cheapest to most expensive, and checked the cancellation rate in each:

| Booking value | Avg. price | Cancellation rate |
|---|---|---|
| Lowest fifth | $259 | 1 in 7 (15.1%) |
| 2nd fifth | $635 | 1 in 7 (14.2%) |
| Middle fifth | $1,026 | 1 in 7 (14.4%) |
| 4th fifth | $1,511 | 1 in 7 (14.8%) |
| Highest fifth | $2,533 | 1 in 7 (15.2%) |

**A guest booking a $2,500 stay is no more or less likely to cancel than one
booking a $250 stay** — value doesn't predict risk any better than topic did
in Section 2.

But it does predict **dollars lost**. Of the roughly **$12.8M in booking
value that cancelled** over the 12 months, **43% of it ($5.6M) sat in just
the top-value fifth of bookings** — because when a $2,500 booking cancels,
it costs 10x what a $250 one does, even though it's no more likely to happen.

**Practical implication:** the "reply to every unanswered opening message"
fix from Section 3 is cheap to do for all 86 affected bookings — but if
support capacity is ever constrained, **work that queue in order of booking
value, highest first.** The dashboard's drill-down view supports sorting the
at-risk list this way.

## 6. Recommended next step

1. Put a response-time alert in place for any guest message that hasn't been
   answered within a set window (e.g. a few hours). This directly targets the
   only pattern in the data that's clearly linked to lost bookings, is cheap
   to implement, and doesn't require guessing at message content or sentiment.
2. When that queue can't be cleared instantly, triage by booking value —
   it doesn't change the odds of cancellation, but it changes how much
   revenue is protected per reply sent.

---

## Appendix: methodology notes (for the analytics team)

- "Cancellation" = booking status `cancelled` vs. `completed`/`confirmed`.
- Lift figures compare the cancellation rate for bookings whose conversation
  contains the pattern vs. the overall 14.75% baseline.
- Message-level sentiment in this dataset is generated from the
  conversation's *topic*, not from an NLP model reading the actual text —
  each topic has a fixed target sentiment (complaints skew negative,
  compliments skew positive, everything else neutral) plus random noise.
  That's why topic and sentiment-based patterns (rows 2–4 in the table
  above) don't discriminate cancellation outcomes: they're not independent
  signals, they're two views of the same topic label. In a production
  system, replacing this with a real text-based sentiment model would be
  the natural next step, and might surface sentiment-driven patterns this
  synthetic data can't.
- Full supporting data: `conversation_journeys.csv`, `stage_impact_analysis.csv`,
  `top10_cancellation_risk.csv`, `cohort_unanswered_opening.csv` (this folder).
