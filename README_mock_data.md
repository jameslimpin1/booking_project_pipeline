# Hotel/Booking SaaS Mock Data — Chat Experience Dataset

Generated with `generate_hotel_chat_data.py`. Default run produces **1,000,000 chat
messages** across ~110K conversations, plus supporting dimension/fact tables — all
as Snappy-compressed Parquet, ~45 MB total on disk.

Peak RAM while generating the full 1M-message dataset in this environment: **~225 MB**.
It'll comfortably run on your 8GB machine, and the same script scales to 10M+ rows
without a redesign (see "Scaling up" below).

## Files

| File | Rows (default run) | What it is |
|---|---|---|
| `dim_users.parquet` | 50,000 | Guests |
| `dim_hosts.parquet` | 4,000 | Hosts |
| `dim_properties.parquet` | 10,000 | Listings |
| `fact_bookings.parquet` | 250,000 | Bookings, linking user → property → host |
| `dim_conversations.parquet` | ~110,000 | One row per chat thread (support or guest↔host) |
| `fact_chat_messages.parquet` | 1,000,000 | Individual chat messages — the main table |

### `fact_chat_messages.parquet` columns
`message_id, conversation_id, sender_type (guest/host/support_agent/bot), sender_id,
sent_at, text, intent_category, sentiment_score, sentiment_label, language,
response_latency_seconds, message_length`

### `dim_conversations.parquet` columns
`conversation_id, booking_id, user_id, host_id, property_id, channel
(in_app/email/whatsapp/sms), intent_category, status (resolved/open/escalated),
opened_at, csat_score (1-5), first_response_time_seconds, resolution_time_seconds,
is_support`

16 `intent_category` values are modeled (pre_booking_question, checkin_issue,
amenity_complaint, refund_request, host_unresponsive, positive_feedback, etc.),
each with realistic template text, sentiment skew, and volume weighting. **CSAT is
deliberately correlated with first-response speed** (slow first response → lower
CSAT) so the dataset actually supports the kind of "how do we improve chat
experience" analysis you're after — e.g. bucket by response time and watch CSAT drop.

`status` and `csat_score` are also kept coherent with how each conversation's
transcript actually ends: a thread can't be `resolved` if it ends without ever
getting a reply (including the single-message "opened, never answered" case),
and CSAT takes an added penalty when the guest's last message went unanswered.
Without this, the generator would independently roll "resolved, CSAT 5" for a
conversation whose last message is a guest question nobody ever replied to —
which is exactly the kind of thing a real chat-QA process would look for.

## Why this stays lean on 8GB RAM

1. **Small dimension tables** (users/hosts/properties) are built once, fully in
   memory — they're small enough (tens of thousands of rows, ~5 columns) that this
   costs a few MB.
2. **Big fact tables** (bookings, conversations, messages) are generated and
   written to Parquet in **chunks** (`--chunk-size`, default 50,000 rows) via
   `pyarrow.parquet.ParquetWriter`. Nothing accumulates the full table in a
   DataFrame — memory use is flat regardless of how many rows you ask for.
3. **Parquet + Snappy** is columnar and compressed, so it's small on disk and
   fast to scan — DuckDB reads only the columns/row-groups a query needs.

## Working with the data locally on 8GB RAM

**Use DuckDB, not pandas, as your primary query engine.** DuckDB queries Parquet
files directly off disk (columnar, predicate pushdown, out-of-core execution) —
you never need to load 1M+ rows into a pandas DataFrame just to filter or
aggregate them.

```bash
pip install duckdb
```

```python
import duckdb
con = duckdb.connect()  # in-memory catalog; data stays on disk

# Cap DuckDB's own memory use explicitly — good practice on constrained machines
con.execute("SET memory_limit='2GB'")
con.execute("SET threads=4")

# Query straight off Parquet, no pandas needed
con.execute("""
    SELECT intent_category, COUNT(*) msgs, AVG(sentiment_score) avg_sentiment
    FROM 'data/fact_chat_messages.parquet'
    GROUP BY 1 ORDER BY 2 DESC
""").df()
```

### Register as a persistent database (optional, nice for repeated analysis)

```python
con = duckdb.connect("hotel_chat.duckdb")   # a file on disk, not in-memory
con.execute("""
    CREATE VIEW messages AS SELECT * FROM 'data/fact_chat_messages.parquet';
    CREATE VIEW conversations AS SELECT * FROM 'data/dim_conversations.parquet';
    CREATE VIEW bookings AS SELECT * FROM 'data/fact_bookings.parquet';
    CREATE VIEW properties AS SELECT * FROM 'data/dim_properties.parquet';
""")
```
Views cost nothing extra on disk — they're just saved SQL pointing at the Parquet
files, so `hotel_chat.duckdb` stays tiny and you get named tables to query from
any tool that speaks DuckDB (Python, CLI, even some BI tools).

### Rules of thumb for staying under 8GB

- **Never do `df = con.execute(...).df()` on the full 1M-row messages table** unless
  you've already filtered/aggregated it down. Aggregate in SQL first, pull the
  (small) result into pandas last.
- **Avoid `SELECT *` on the whole table** — Parquet's columnar layout means
  `SELECT text, sentiment_score FROM ...` only reads those two columns off disk;
  `SELECT *` reads everything.
- **Push joins and filters into DuckDB**, not pandas — DuckDB will use the
  `first_response_time_seconds`/date columns to prune Parquet row-groups instead
  of scanning everything.
- **Chunk your own downstream processing** the same way the generator does: if
  you're doing something row-by-row in Python (e.g. calling an LLM per message),
  iterate with `con.execute(...).fetch_arrow_reader(batch_size=...)` or `LIMIT`/
  `OFFSET` paging rather than materializing the whole table first.
- If you do need pandas for a specific chunk: `con.execute("... LIMIT 100000 OFFSET
  0").df()` and iterate.

### Example: build a "chat experience" summary without ever touching pandas on the big table

```python
con.execute("""
    SELECT
        c.channel,
        c.intent_category,
        COUNT(*) AS conversations,
        ROUND(AVG(c.csat_score), 2) AS avg_csat,
        ROUND(AVG(c.first_response_time_seconds), 0) AS avg_first_response_s,
        ROUND(100.0 * SUM(CASE WHEN c.status='escalated' THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_escalated
    FROM 'data/dim_conversations.parquet' c
    GROUP BY 1, 2
    ORDER BY conversations DESC
""").df()   # this result is small (channels x intents), safe to bring into pandas
```

## Scaling up

```bash
# 10M messages, still bounded memory (just takes longer / more disk):
python3 generate_hotel_chat_data.py --out-dir ./data_10m --n-messages 10000000 \
    --n-bookings 1000000 --n-users 200000 --n-properties 30000 --chunk-size 100000
```

Rule of thumb: peak RAM tracks `--chunk-size`, not `--n-messages`. If you're ever
memory-constrained, lower `--chunk-size` (e.g. to 10,000) — it'll write more,
smaller Parquet row-groups but use even less RAM.

## Regenerating / customizing

All the volumes are CLI flags — see `python3 generate_hotel_chat_data.py --help`.
Useful knobs:
- `--avg-msgs-per-convo` — controls conversation length distribution (Poisson-ish)
- `--n-support-agents` — size of the support agent pool for support-intent threads
- `--seed` — change for a different random dataset; same seed = reproducible

To change the *content* (add new intent categories, new message templates, new
languages), edit the `INTENTS`, `INTENT_WEIGHTS`, and `TEMPLATES` dict near the
top of the script — templates are split into `"guest"` and `"other"` phrasing per
intent so sender and message text stay consistent.
