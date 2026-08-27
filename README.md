# Booking Messaging & Rewards — Analytics Pipeline

A portfolio project simulating a Data Engineer role on Booking.com's Messaging &
Rewards team: a medallion-architecture analytics pipeline built with **dbt** and
**DuckDB** on top of a synthetic hotel chat/booking dataset, feeding the
["Why We Lose Bookings" dashboard](https://github.com/jameslimpin1/booking_project_dashboard)
(separate repo — see [Keeping the dashboard fresh](#keeping-the-dashboard-fresh)).

## Stack

- **DuckDB** — embedded warehouse (`warehouse/booking.duckdb`, gitignored — rebuild with `dbt run`)
- **dbt-duckdb** — data modeling, testing, documentation
- **Parquet** (`data/*.parquet`) — synthetic raw source files, read directly by dbt sources via `read_parquet()`

This repo is the data pipeline only. The static HTML/JS dashboard that
consumes its marts lives in a separate repo,
[booking_project_dashboard](https://github.com/jameslimpin1/booking_project_dashboard) —
split out so the dashboard can be deployed (e.g. GitHub Pages) independently
of the pipeline's Python/dbt tooling.

## Data architecture

Medallion layout, each layer a dbt model directory:

```
data/*.parquet  →  models/staging/  →  models/intermediate/  →  models/marts/  →  booking_project_dashboard
   (raw)              (1:1 views)         (joins, business          (consumption-        (separate repo,
                                             logic)                   ready tables)        static site)
```

| Layer | Location | Materialization | Purpose |
|---|---|---|---|
| Staging | `models/staging/` | view | 1:1 views over raw source Parquet, light renaming/casting only |
| Intermediate | `models/intermediate/` | table | Joins, business logic, conversation-journey and risk-scoring transformations |
| Marts | `models/marts/` | table | Consumption-ready tables powering the dashboard |

### Sources (raw data)

Registered in `models/staging/_staging__sources.yml`, pointed at `data/*.parquet`.
Grain and columns are documented there; see also [README_mock_data.md](README_mock_data.md)
for how the synthetic dataset is generated.

- `dim_users` — one row per guest
- `dim_hosts` — one row per property host
- `dim_properties` — one row per listing
- `fact_bookings` — one row per booking (user → property → host)
- `dim_conversations` — one row per chat thread tied to a booking
- `fact_chat_messages` — one row per individual chat message

### Staging models

One view per source table (`stg_dim_users`, `stg_fact_bookings`, etc.) — renaming/
casting only, no business logic.

### Intermediate models (`models/intermediate/`)

- **`int_booking_cancellation_risk`** — one row per confirmed booking; rule-based
  composite `cancellation_risk_score` (0–1) from conversation/message signals
  (intent, sentiment, CSAT, escalation, response speed). Flags the top risk decile.
- **`int_conversation_log`** — one row per chat message, sequenced within its
  conversation (`message_seq`, first/last flags, time since previous message).
- **`int_conversation_journeys`** — collapses the message-grain log to one row per
  conversation: full event/sentiment journey strings, opening/closing sentiment,
  escalation and delayed-reply counts. Shared base for every mart below.
- **`int_at_risk_conversations`** / **`int_at_risk_conversation_concerns`** —
  drill-down into the top-risk-decile segment and its dominant concerns.

### Marts (`models/marts/`)

Each mart is consumption-ready and maps to a specific piece of the dashboard:

| Mart | Grain | Powers |
|---|---|---|
| `mart_booking_loss_events` | 1 row per conversation (trailing 12mo) | Master fact table: `journey_stage` (WHERE it went wrong) + `event_pattern` (WHAT happened, mutually exclusive) |
| `mart_loss_by_month_pattern` | month × channel × loyalty_tier × event_pattern | Month × event-pattern impact heatmap |
| `mart_loss_by_stage` | month × channel × loyalty_tier × journey_stage | "Where in the journey we lose bookings" table + revenue-lost hero stats |
| `mart_complaint_cohort` | month × channel × loyalty_tier × intent_category | Top-5 complaints / everyday questions, loyalty-tier breakdown |
| `mart_price_quintile_risk` | price quintile (1–5) | "Booking value doesn't predict who cancels, but predicts what it costs" chart |
| `mart_unanswered_opening_monthly` | month | Monthly cohort trend for the "unanswered opening message" pattern — the strongest single driver found (1.58x baseline cancellation rate) |

Full column-level docs live in `models/marts/_marts__models.yml` and are browsable
via `dbt docs generate && dbt docs serve`.

### Dashboard ([booking_project_dashboard](https://github.com/jameslimpin1/booking_project_dashboard), separate repo)

Static HTML/JS prototype consuming the marts above:

- `index.html` — main "Why We Lose Bookings" report (KPIs, loss-by-stage table, month × pattern heatmap, complaint cohorts, price-quintile chart). Single canonical entry point, served at the repo's Pages root.
- `dashboard_prototype_page2.html` — customer-journey / Sankey drill-down view, reachable from `index.html`'s nav bar

Its data is exported from this repo's warehouse by `scripts/export_dashboard_data.py` —
see [Keeping the dashboard fresh](#keeping-the-dashboard-fresh).

### Analysis (`analysis/`)

Exec-summary writeups and CSV extracts used to prep the dashboard content — see
[analysis/README.md](analysis/README.md) for the underlying findings
(cancellation-risk scoring, stage-loss analysis, top concerns).

## Setup

```bash
cd booking_project_pipeline
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`profiles.yml` targets `warehouse/booking.duckdb` (profile `booking_messaging_rewards`, target `dev`).

## Running the pipeline

```bash
dbt run                      # build all models
dbt test                     # run schema/data tests
dbt run --select staging     # build just the staging layer
dbt run --select marts       # build just the marts layer
dbt docs generate && dbt docs serve   # browse full column-level documentation
```

> DuckDB allows only one writer at a time — close any other connection (e.g. a
> VSCode DuckDB extension) holding `warehouse/booking.duckdb` before running
> `dbt`, or open read-only: `duckdb -readonly warehouse/booking.duckdb`.

All paths in `_staging__sources.yml` are relative to the project root — run
`dbt`/`duckdb` commands from `booking_project_pipeline/`, not a subdirectory.

### Materialization strategy

Staging is `view` (cheap passthrough, always reflects the current source);
intermediate and marts are `table` (full recompute every run). Several
models depend on whole-population or rolling-window statistics —
`int_booking_cancellation_risk` (percent_rank across all confirmed
bookings), `mart_booking_loss_events` (trailing-12-month window anchored to
`max(conversation_started_at)`, so old rows age *out* as new data arrives),
and `mart_price_quintile_risk` (ntile quintiles across the full population).
An incremental, append-only load would silently go stale for these — a
previously-computed percentile/quintile/window boundary doesn't get
revisited when new data shifts it, and everything downstream inherits that
staleness. Given this dataset's size (72K–110K rows), a full rebuild of the
whole pipeline runs in ~2-3 seconds, so there's no real performance case for
trading that correctness away — every model here stays a full recompute.

## Keeping the dashboard fresh

`booking_project_dashboard`'s HTML files embed their chart/drill-down data as
static JSON (`<script type="application/json">` blocks) rather than querying
the warehouse live, so they go stale whenever this repo's data or marts
change. `scripts/refresh_pipeline.sh` re-syncs them:

```bash
scripts/refresh_pipeline.sh
```

By default this expects `booking_project_dashboard` checked out as a sibling
directory (`../booking_project_dashboard`) — clone both repos side by side.
Point elsewhere with `--dashboard-dir PATH` or `$BOOKING_DASHBOARD_DIR`:

```bash
scripts/refresh_pipeline.sh --dashboard-dir /path/to/booking_project_dashboard
```

It runs, in order: `dbt build` (rebuild marts against current
`data/*.parquet`), `dbt source freshness` (reports staleness, non-blocking),
then `scripts/export_dashboard_data.py`, which re-queries the marts and
replaces each dashboard file's JSON blocks in place — everything else in the
HTML (layout, JS, styling) is left untouched. The drill-down conversation set
(`dd-data`) is reselected deterministically (hash-based, fixed seed, stable
across `dbt build --full-refresh` rebuilds — a naive reservoir sample is not,
since DuckDB doesn't guarantee physical row order across a table rebuild) so
it's the same 216 conversations on every re-run against unchanged data.

This script does **not** regenerate synthetic data or touch git in either
repo — run `python3 generate_hotel_chat_data.py` yourself first if you want a
new dataset, and commit/push `booking_project_dashboard`'s changes separately
after the refresh.

### Source freshness

`dim_conversations` and `fact_chat_messages` have a `freshness` check
(`warn_after: 4 hours`, `error_after: 12 hours`, checked via `loaded_at_field:
generated_at`) — `dbt source freshness` flags if the data hasn't been
regenerated recently. Run it standalone with:

```bash
dbt source freshness
```

### Automating the refresh

There's no cron job installed for this yet — installing one requires Full
Disk Access for your terminal app (System Settings → Privacy & Security),
which is a machine-level change only you can make. To run the refresh
weekly, add this via `crontab -e`:

```
0 6 * * 1 cd /Users/limpij/Documents/gruntwork_/booking_project_pipeline && ./scripts/refresh_pipeline.sh >> logs/refresh_cron.log 2>&1
```

## Testing

Every primary/surrogate key is tested `unique` + `not_null`, every foreign key
has a `relationships` test, and bounded/numeric columns get a lightweight
sanity check — enough to catch a broken join or a silently-wrong CASE
expression without testing every column on every model.

Four custom generic tests live in `macros/generic_tests/` and are reused
across staging, intermediate, and marts models:

| Test | Checks | Used for |
|---|---|---|
| `between(min_value, max_value)` | column falls within a range | scores/percentiles (0–1), CSAT (1–5), ratings (0–5), percentages (0–100), sentiment (-1–1) |
| `non_negative` | column is not < 0 | prices, counts, durations, latencies |
| `unique_combination_of_columns(combination_of_columns)` | no duplicate rows across a set of columns | composite-grain marts, e.g. `(month, channel, loyalty_tier, event_pattern)` |
| `not_constant` | column has more than one distinct value | catches a model silently collapsing to one value |

Two more, in `macros/mart_tests/`, reconcile mart-level aggregates back to
their upstream model so a broken join or a filter that silently drops rows
shows up as a test failure, not a wrong dashboard number:

| Test | Checks | Used for |
|---|---|---|
| `value_matches_upstream(column_name, compare_model, join_key)` | row-level: column matches the same key's value upstream | `mart_booking_loss_events.total_price` vs. `stg_fact_bookings.total_price` per `booking_id` |
| `agg_matches_upstream(column_name, compare_model, model_agg, compare_agg, compare_where, tolerance)` | an aggregate (sum/count/avg) on this model matches the same aggregate upstream, within a rounding tolerance | e.g. `sum(cancelled_price)` in `mart_loss_by_stage` reproduces `sum(case when is_cancelled then total_price else 0 end)` in `mart_booking_loss_events` |

```bash
dbt test              # run all tests
dbt build              # run models + tests together
```

## Roadmap

- [x] Staging + intermediate layers
- [x] Marts layer powering the dashboard
- [x] Dashboard prototype (loss-by-stage, month × pattern heatmap, complaint cohorts, price-quintile risk)
- [x] Schema tests (`unique`/`not_null`/`relationships` + custom generic tests) across staging, intermediate, and marts models
- [x] Split into separate pipeline/dashboard repos, with a cross-repo refresh script
