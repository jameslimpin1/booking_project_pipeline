# Booking Messaging & Rewards — Analytics Pipeline

A portfolio project simulating a Data Engineer role on Booking.com's Messaging &
Rewards team: a medallion-architecture analytics pipeline built with **dbt** and
**DuckDB** on top of a synthetic hotel chat/booking dataset.

For details on the underlying mock dataset (schema, volumes, generation script),
see the [root README](../README.md).

## Stack

- **DuckDB** — embedded warehouse (`warehouse/booking.duckdb`)
- **dbt-duckdb** — data modeling, testing, documentation
- **Parquet** (`data/*.parquet`) — raw source files, read directly by dbt sources

## Architecture

Medallion layout:

| Layer | Location | Purpose |
|---|---|---|
| Staging | `models/staging/` | 1:1 views over raw source Parquet, light renaming/casting only |
| Intermediate | `models/intermediate/` | Joins, business logic, transformations |
| Marts | `models/marts/` *(planned)* | Consumption-ready tables for analysis/reporting |

### Sources

Raw tables are registered in `models/staging/_staging__sources.yml` and point at
`data/*.parquet` via DuckDB's `read_parquet()`:

- `dim_users`, `dim_hosts`, `dim_properties`
- `fact_bookings`
- `dim_conversations`, `fact_chat_messages`

### Staging models

One staging model per source table (`stg_dim_conversations`, `stg_fact_bookings`,
etc.), each materialized as a `view` tagged `staging`.

### Intermediate models

All in `models/intermediate/`, materialized as `view`s tagged `intermediate`.
First slice: identifying the highest cancellation-risk bookings and the top
concerns driving them.

- **`int_booking_cancellation_risk`** — one row per confirmed booking, with a
  rule-based composite `cancellation_risk_score` (0–1) built from linked
  conversation/message signals: cancellation/refund intent, negative
  sentiment, % negative messages, low CSAT, escalation, and slow response/
  resolution times (weighted 0.25/0.20/0.15/0.15/0.10/0.05/0.05/0.05). Flags
  the top 10% by score as `is_top_risk_decile`.
- **`int_conversation_log`** — one row per chat message, sequenced within its
  conversation (`message_seq`, `is_first_message`/`is_last_message`,
  `seconds_since_prev_message`) — the full event log for every conversation.
- **`int_at_risk_conversations`** — conversations belonging to top-risk-decile
  bookings; drill-down artifact for inspecting the frustrated-customer segment.
- **`int_at_risk_conversation_concerns`** — global top-5 concerns
  (`intent_category`) by conversation count across the at-risk segment.

## Setup

```bash
cd booking_project_pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install dbt-duckdb
```

`profiles.yml` is already configured to use `warehouse/booking.duckdb` as the
target (profile `booking_messaging_rewards`, target `dev`).

## Running the pipeline

```bash
dbt run           # build all models
dbt test          # run schema/data tests
dbt run --select staging   # build just the staging layer
```

## Querying the data

**Preview a model without building it** (compiles + runs SQL against the raw
Parquet on the fly):

```bash
dbt show --select stg_dim_conversations --limit 10
```

**Query the built warehouse directly** with the DuckDB CLI (`brew install duckdb`):

```bash
duckdb warehouse/booking.duckdb
```

> Note: DuckDB only allows one writer/connection to hold the file lock at a
> time. If a VSCode DuckDB extension (or another process) has
> `booking.duckdb` open, `dbt run` and the CLI will fail with a lock error.
> Close the other connection first, or open the CLI read-only:
> `duckdb -readonly warehouse/booking.duckdb`.

All paths in `_staging__sources.yml` are relative to the project root — run
`dbt`/`duckdb` commands from `booking_project_pipeline/`, not a subdirectory.

## Testing

Staging models currently have no schema tests defined. Planned:
- `unique` / `not_null` on primary keys (`conversation_id`, `booking_id`, `message_id`, etc.)
- `relationships` tests across fact/dim foreign keys
- Singular tests under `tests/` for business-rule assertions (e.g. no orphan conversations)

## Roadmap

- [x] Intermediate layer: join conversations to bookings/properties/hosts;
      cancellation-risk scoring and at-risk-segment concerns
- [ ] Marts: messaging performance (response time, CSAT), rewards/engagement funnels
- [ ] Schema tests + data quality checks on staging and marts
- [ ] dbt docs generation (`dbt docs generate && dbt docs serve`)
