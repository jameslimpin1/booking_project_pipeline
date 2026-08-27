#!/usr/bin/env python3
"""
Refreshes the JSON data embedded in index.html / dashboard_prototype_page2.html
(in the separate booking_project_dashboard repo) from the current dbt
warehouse in this repo. Run this after `dbt build` (see
scripts/refresh_pipeline.sh) -- it does not run dbt itself.

Each dashboard file embeds its data as one or more
<script id="..." type="application/json">{"cols": [...], "rows": [[...], ...]}</script>
blocks. This script re-queries the marts that feed each block, rebuilds the
JSON with the exact same shape, and replaces the block in place -- everything
else in the HTML (layout, JS, styling) is left untouched.

conv-profile-data (the "Conversation profile" small-multiples section)
aggregates the FULL population in mart_booking_loss_events by month, not the
curated drill-down set below -- the drill-down set is risk-weighted and
would misrepresent the true mix if used for that section.

The drill-down conversation set (dd-data) is chosen deterministically so the
same 216 conversations come back on every re-run against unchanged data:
every unanswered_opening conversation (currently 86), the 50 highest
cancellation-risk bookings, and an 80-conversation deterministic sample of
everything else -- see the card copy in dashboard_prototype_page2.html.

Requires booking_project_dashboard to be checked out as a sibling directory
by default (../booking_project_dashboard) -- override with --dashboard-dir
or the BOOKING_DASHBOARD_DIR env var if it lives elsewhere.
"""
import argparse
import json
import os
import re
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WAREHOUSE_PATH = PROJECT_ROOT / "warehouse" / "booking.duckdb"
DEFAULT_DASHBOARD_DIR = PROJECT_ROOT.parent / "booking_project_dashboard"

HIGH_RISK_N = 50
TYPICAL_N = 80
SAMPLE_SEED = 42


def rows_and_cols(con, sql):
    result = con.execute(sql)
    cols = [d[0] for d in result.description]
    rows = [list(r) for r in result.fetchall()]
    return {"cols": cols, "rows": rows}


def build_rev_agg_data(con):
    return rows_and_cols(con, """
        select
            month, channel, loyalty_tier, journey_stage as stage,
            bookings, cancelled_bookings, total_price, cancelled_price
        from main_marts.mart_loss_by_stage
        order by month, channel, loyalty_tier, stage
    """)


def build_agg_data(con):
    intent = rows_and_cols(con, """
        select
            month, channel, loyalty_tier, intent_category, topic_cluster as cluster,
            bookings, cancelled_bookings, total_price, cancelled_price
        from main_marts.mart_complaint_cohort
        order by month, channel, loyalty_tier, intent_category
    """)
    journey = rows_and_cols(con, """
        select month, channel, loyalty_tier, journey_stage as stage, bookings, cancelled_bookings
        from main_marts.mart_loss_by_stage
        order by month, channel, loyalty_tier, stage
    """)
    totals = rows_and_cols(con, """
        select month, channel, loyalty_tier, sum(bookings) as bookings, sum(cancelled_bookings) as cancelled_bookings
        from main_marts.mart_loss_by_stage
        group by 1, 2, 3
        order by 1, 2, 3
    """)
    return {"intent": intent, "journey": journey, "totals": totals}


def build_impact_agg_data(con):
    return rows_and_cols(con, """
        select
            month, channel, loyalty_tier, event_pattern as category,
            bookings, cancelled_bookings, cancelled_price
        from main_marts.mart_loss_by_month_pattern
        order by month, channel, loyalty_tier, category
    """)


def build_conv_profile_data(con):
    # Month x value counts over the FULL population (mart_booking_loss_events,
    # ~72K conversations) for each of 6 dimensions -- deliberately NOT the
    # curated 216-conversation drill-down set, which is risk-weighted and
    # would misrepresent the true mix (e.g. CSAT skews heavily low there).
    # Client-side JS sums across whatever month range the date slider covers.
    dimension_exprs = {
        "booking_outcome": "booking_status",
        "primary_intent": "conversation_primary_intent",
        "channel": "conversation_channel",
        "loyalty_tier": "loyalty_tier",
        "csat": "case when csat_score is null then '—' else csat_score::varchar || ' / 5' end",
        "messages": """
            case
                when conversation_message_count <= 1 then '1'
                when conversation_message_count <= 3 then '2–3'
                when conversation_message_count <= 6 then '4–6'
                when conversation_message_count <= 10 then '7–10'
                else '11+'
            end
        """,
    }
    return {
        key: rows_and_cols(con, f"""
            select
                activity_month as month,
                {expr} as value,
                count(*) as count
            from main_marts.mart_booking_loss_events
            group by 1, 2
            order by 1, 2
        """)
        for key, expr in dimension_exprs.items()
    }


def build_dd_data(con):
    # order by conversation_id here (and nowhere else naturally sorts this
    # query) so tags/selected_ids below have a stable, rebuild-independent
    # iteration order -- otherwise the *set* of unanswered_opening ids is
    # correct but their order (and so the tags dict's key order in the
    # output JSON) varies with table scan order, producing a spurious diff
    # on every refresh even when nothing actually changed.
    unanswered_ids = [r[0] for r in con.execute("""
        select conversation_id from main_marts.mart_booking_loss_events
        where event_pattern = 'unanswered_opening'
        order by conversation_id
    """).fetchall()]

    high_risk_ids = [r[0] for r in con.execute("""
        select m.conversation_id
        from main_marts.mart_booking_loss_events m
        inner join main_intermediate.int_booking_cancellation_risk r using (booking_id)
        where m.conversation_id not in (select unnest($unanswered))
        order by r.cancellation_risk_score desc, m.conversation_id
        limit $n
    """, {"unanswered": unanswered_ids, "n": HIGH_RISK_N}).fetchall()]

    excluded = unanswered_ids + high_risk_ids
    # Deterministic by conversation_id value, not table scan order: DuckDB's
    # `USING SAMPLE ... (reservoir, seed)` only reproduces the same rows if
    # the table's physical scan order is unchanged, which a `dbt build
    # --full-refresh` rebuild does not guarantee even for identical data
    # (confirmed by hand: reservoir sampling silently returned a different
    # 80 conversations across two otherwise-identical rebuilds). Ordering by
    # a hash of the id salted with a fixed seed depends only on the id
    # values, so it's stable across rebuilds.
    typical_ids = [r[0] for r in con.execute(f"""
        select conversation_id
        from main_marts.mart_booking_loss_events
        where conversation_id not in (select unnest($excluded))
        order by hash(conversation_id::varchar || '_{SAMPLE_SEED}')
        limit {TYPICAL_N}
    """, {"excluded": excluded}).fetchall()]

    selected_ids = unanswered_ids + high_risk_ids + typical_ids

    con.execute(
        "create or replace temp table selected_conv_ids as select unnest($ids) as conversation_id",
        {"ids": selected_ids},
    )

    meta = rows_and_cols(con, """
        select
            m.conversation_id, m.booking_id, m.conversation_channel as channel,
            m.conversation_status as convo_status, m.csat_score,
            m.conversation_primary_intent as primary_intent, m.booking_status,
            m.loyalty_tier, m.activity_month as month
        from main_marts.mart_booking_loss_events m
        inner join selected_conv_ids s using (conversation_id)
        order by m.conversation_id
    """)

    messages = rows_and_cols(con, """
        select l.conversation_id, l.message_seq, l.sender_type,
               strftime(l.sent_at, '%Y-%m-%d %H:%M:%S') as sent_at,
               l.text, l.sentiment_label, l.event_label
        from main_intermediate.int_conversation_log l
        inner join selected_conv_ids s using (conversation_id)
        order by l.conversation_id, l.message_seq
    """)

    tags = {}
    for cid in unanswered_ids:
        tags.setdefault(str(cid), []).append("unanswered_opening")
    for cid in high_risk_ids:
        tags.setdefault(str(cid), []).append("high_risk")
    for cid in typical_ids:
        tags.setdefault(str(cid), []).append("typical")

    return {"meta": meta, "messages": messages, "tags": tags}


def replace_script_block(html, block_id, payload):
    pattern = re.compile(
        r'(<script id="' + re.escape(block_id) + r'" type="application/json">)(.*?)(</script>)',
        re.S,
    )
    new_json = json.dumps(payload, separators=(",", ":"), default=str)
    new_html, n = pattern.subn(lambda m: m.group(1) + new_json + m.group(3), html, count=1)
    if n == 0:
        raise RuntimeError(f'<script id="{block_id}"> not found')
    return new_html


def refresh_file(path: Path, blocks: dict):
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found -- is booking_project_dashboard checked out there? "
            "Pass --dashboard-dir or set BOOKING_DASHBOARD_DIR to point at it."
        )
    html = path.read_text()
    for block_id, payload in blocks.items():
        html = replace_script_block(html, block_id, payload)
    path.write_text(html)
    print(f"  updated {path}: {', '.join(blocks)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--warehouse", default=str(WAREHOUSE_PATH))
    ap.add_argument(
        "--dashboard-dir",
        default=os.environ.get("BOOKING_DASHBOARD_DIR", str(DEFAULT_DASHBOARD_DIR)),
        help="Path to the booking_project_dashboard repo (default: sibling directory, "
             "or $BOOKING_DASHBOARD_DIR).",
    )
    args = ap.parse_args()
    dashboard_dir = Path(args.dashboard_dir).resolve()

    con = duckdb.connect(args.warehouse, read_only=True)

    rev_agg_data = build_rev_agg_data(con)
    agg_data = build_agg_data(con)
    impact_agg_data = build_impact_agg_data(con)
    conv_profile_data = build_conv_profile_data(con)
    dd_data = build_dd_data(con)
    con.close()

    print(f"Refreshing dashboard HTML in {dashboard_dir} from the current warehouse...")
    refresh_file(dashboard_dir / "index.html", {
        "rev-agg-data": rev_agg_data,
    })
    refresh_file(dashboard_dir / "dashboard_prototype_page2.html", {
        "agg-data": agg_data,
        "rev-agg-data": rev_agg_data,
        "impact-agg-data": impact_agg_data,
        "conv-profile-data": conv_profile_data,
        "dd-data": dd_data,
    })
    print("Done.")


if __name__ == "__main__":
    main()
