#!/usr/bin/env python3
"""
generate_hotel_chat_data.py

Generates a mock dataset for a hotel/booking SaaS (Booking.com / Airbnb style)
with a focus on customer-chat data, at scale (default: 1,000,000 chat messages).

Design goals (so this runs fine on an 8GB RAM laptop):
  - Small dimension tables (users, hosts, properties) are built once, fully
    in memory, as compact numpy arrays -- not fat Python objects.
  - The big fact tables (bookings, conversations, chat_messages) are
    generated and written to Parquet in CHUNKS via pyarrow.ParquetWriter,
    so peak memory is bounded by --chunk-size, not by the total row count.
    Doubling --n-messages does NOT double peak RAM usage.
  - Text is built from templates + placeholders (fast, vectorizable-ish),
    not a heavy NLP/Faker call per row -- Faker is only used up front for
    the small dimension tables (names, cities, companies), which is cheap.
  - Output is Snappy-compressed Parquet, columnar and small on disk,
    queryable directly with DuckDB without ever loading it all into pandas.

Output tables (in --out-dir):
  dim_users.parquet
  dim_hosts.parquet
  dim_properties.parquet
  fact_bookings.parquet
  dim_conversations.parquet
  fact_chat_messages.parquet     <-- the big one, ~1M+ rows

Usage:
  python3 generate_hotel_chat_data.py --n-messages 1000000 --out-dir ./data

  # Bigger run, still bounded memory:
  python3 generate_hotel_chat_data.py --n-messages 10000000 --chunk-size 100000 --out-dir ./data
"""

import argparse
import datetime as dt
import os
import random
import string
import sys

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

try:
    from faker import Faker
except ImportError:
    print("Please `pip install faker` (or run with --break-system-packages).", file=sys.stderr)
    raise


# --------------------------------------------------------------------------
# Config / reference data
# --------------------------------------------------------------------------

PROPERTY_TYPES = ["Apartment", "Hotel Room", "Villa", "Cabin", "Studio", "Loft", "Guesthouse", "Resort Suite"]
CHANNELS = ["web", "ios", "android"]
CHAT_CHANNELS = ["in_app", "email", "whatsapp", "sms"]
LANGUAGES = ["en", "es", "fr", "de", "pt", "it"]
BOOKING_STATUSES = ["confirmed", "completed", "cancelled"]
BOOKING_STATUS_WEIGHTS = [0.35, 0.50, 0.15]
CONVO_STATUSES = ["resolved", "open", "escalated"]
SENDER_TYPES = ["guest", "host", "support_agent", "bot"]

# Chat intent categories that matter for "optimizing customer chat experience"
INTENTS = [
    "pre_booking_question",
    "checkin_instructions",
    "checkin_issue",
    "amenity_complaint",
    "cleanliness_complaint",
    "noise_complaint",
    "cancellation_request",
    "refund_request",
    "payment_issue",
    "extend_stay_request",
    "early_checkout",
    "lost_item",
    "host_unresponsive",
    "wifi_issue",
    "general_inquiry",
    "positive_feedback",
]
# Rough real-world-ish weighting: lots of pre-booking Qs and general inquiries,
# fewer but costlier complaint/refund threads.
INTENT_WEIGHTS = np.array([
    0.17, 0.10, 0.06, 0.05, 0.04, 0.03,
    0.08, 0.06, 0.05, 0.04, 0.03, 0.02,
    0.03, 0.05, 0.14, 0.05,
])
INTENT_WEIGHTS = INTENT_WEIGHTS / INTENT_WEIGHTS.sum()

# Message templates per intent, tagged by speaker side:
#   "guest" -> only ever sent by the guest
#   "other" -> sent by whoever is on the other side of the thread
#              (host, support_agent, or bot depending on the conversation)
# This keeps sender_type and message content consistent (a host/agent
# template never gets put in a guest's mouth and vice versa).
TEMPLATES = {
    "pre_booking_question": {
        "guest": [
            "Hi, does {property_name} have {amenity}?",
            "Is {property_name} available from {checkin} to {checkout}?",
            "What's the total price for {nights} nights at {property_name}?",
            "Can I bring a pet to {property_name}?",
            "Is parking included at {property_name}?",
        ],
        "other": [
            "Sure, {property_name} has {amenity} and is available those dates.",
            "The total for {nights} nights would be ${amount}.",
            "Yes, pets are welcome at {property_name} for a small fee.",
        ],
    },
    "checkin_instructions": {
        "guest": [
            "What time is check-in at {property_name}?",
            "How do I get the keys for {property_name}?",
        ],
        "other": [
            "Check-in is from 3PM. The door code will be sent 1 hour before arrival.",
            "You'll find the lockbox to the right of the front door, code {code}.",
        ],
    },
    "checkin_issue": {
        "guest": [
            "We're outside {property_name} but the door code isn't working.",
            "We've been waiting 20 minutes and can't get in.",
        ],
        "other": [
            "I'm so sorry, let me get that fixed right away, one moment please.",
            "I've reset the code, please try {code} now.",
        ],
    },
    "amenity_complaint": {
        "guest": [
            "The {amenity} at {property_name} isn't working.",
            "There's no {amenity} as advertised in the listing.",
        ],
        "other": [
            "I apologize for the inconvenience, I'll send a technician today.",
            "We're sorry about that, we'll issue a partial refund for the inconvenience.",
        ],
    },
    "cleanliness_complaint": {
        "guest": [
            "The room at {property_name} wasn't clean when we arrived.",
            "There was trash left from the previous guest.",
        ],
        "other": [
            "I'm very sorry to hear that, can you share a photo so I can escalate?",
            "We've dispatched housekeeping immediately, truly sorry about this.",
        ],
    },
    "noise_complaint": {
        "guest": [
            "It's very noisy at {property_name}, we can't sleep.",
            "Construction started at 7AM right outside our window.",
        ],
        "other": [
            "I apologize, I'll reach out to the neighbors right away.",
            "I'm sorry about the disruption, would you like to relocate?",
        ],
    },
    "cancellation_request": {
        "guest": [
            "I need to cancel my booking at {property_name}.",
            "Can I cancel without a penalty? Something came up.",
        ],
        "other": [
            "I understand, I can process that cancellation for you now.",
            "Per the cancellation policy, you're eligible for a {amount}% refund.",
        ],
    },
    "refund_request": {
        "guest": [
            "When will I get my refund for {property_name}?",
            "I was charged twice for my stay at {property_name}.",
        ],
        "other": [
            "Refunds are processed within 5-7 business days.",
            "I see the duplicate charge, refunding ${amount} now, sorry about that.",
        ],
    },
    "payment_issue": {
        "guest": [
            "My card was declined when booking {property_name}.",
            "I was charged ${amount} more than the listed price.",
        ],
        "other": [
            "Let's try a different payment method, or I can send a payment link.",
            "That looks like a currency conversion fee from your bank, not us.",
        ],
    },
    "extend_stay_request": {
        "guest": [
            "Can we extend our stay at {property_name} by {nights} more nights?",
        ],
        "other": [
            "Let me check availability... yes, that works, I'll send an updated invoice.",
            "Unfortunately the property is booked after your checkout date.",
        ],
    },
    "early_checkout": {
        "guest": [
            "We need to check out early from {property_name}, is that okay?",
            "Will I be refunded for the unused nights?",
        ],
        "other": [
            "No problem at all, just leave the keys in the lockbox.",
        ],
    },
    "lost_item": {
        "guest": [
            "I think I left my {item} at {property_name}, can you check?",
        ],
        "other": [
            "Let me ask the host to check the room and get back to you.",
            "Good news, the host found your {item}, we can ship it to you.",
        ],
    },
    "host_unresponsive": {
        "guest": [
            "The host hasn't replied in 2 days about {property_name}.",
            "Still no response, can support step in?",
        ],
        "other": [
            "I'm sorry for the delay, I'll follow up with the host directly.",
        ],
    },
    "wifi_issue": {
        "guest": [
            "The wifi at {property_name} isn't working.",
            "Still can't connect, is there a router I can reset?",
        ],
        "other": [
            "Sorry about that! The network name is {code} and password is {code}2024.",
        ],
    },
    "general_inquiry": {
        "guest": [
            "Do you have a local guide for the area around {property_name}?",
            "What's the best way to get to {property_name} from the airport?",
            "Is there a grocery store within walking distance?",
        ],
        "other": [
            "Sure! Here are a few recommendations near {property_name}.",
        ],
    },
    "positive_feedback": {
        "guest": [
            "We had an amazing stay at {property_name}, thank you!",
            "{property_name} exceeded our expectations, 5 stars!",
        ],
        "other": [
            "So glad to hear that, hope to host you again soon!",
            "Thank you so much for the kind words!",
        ],
    },
}

AMENITIES = ["wifi", "a pool", "air conditioning", "free parking", "a washer", "a hot tub", "a kitchen", "heating"]
ITEMS = ["phone charger", "sunglasses", "jacket", "passport", "camera", "kid's toy"]


def rand_code(rng: random.Random, n=6):
    return "".join(rng.choices(string.ascii_uppercase + string.digits, k=n))


def fill_template(rng: random.Random, template: str, property_name: str, checkin: dt.date, checkout: dt.date):
    nights = max(1, (checkout - checkin).days)
    return template.format(
        property_name=property_name,
        amenity=rng.choice(AMENITIES),
        checkin=checkin.isoformat(),
        checkout=checkout.isoformat(),
        nights=nights,
        amount=rng.choice([15, 20, 25, 30, 40, 50, 75, 100, 120]),
        code=rand_code(rng, 4),
        item=rng.choice(ITEMS),
    )


# --------------------------------------------------------------------------
# Dimension table builders (small, built once, kept fully in memory)
# --------------------------------------------------------------------------

def build_users(n_users: int, seed: int) -> pa.Table:
    fk = Faker()
    Faker.seed(seed)
    rng = np.random.default_rng(seed)
    ids = np.arange(1, n_users + 1)
    names = [fk.name() for _ in range(n_users)]
    countries = rng.choice(
        ["US", "UK", "DE", "FR", "ES", "IT", "BR", "MX", "CA", "AU", "JP", "IN"], size=n_users
    )
    signup_dates = np.array(
        [dt.date(2022, 1, 1) + dt.timedelta(days=int(d)) for d in rng.integers(0, 1400, size=n_users)]
    )
    loyalty = rng.choice(["none", "silver", "gold", "platinum"], size=n_users, p=[0.6, 0.25, 0.1, 0.05])
    table = pa.table({
        "user_id": pa.array(ids, type=pa.int32()),
        "full_name": pa.array(names, type=pa.string()),
        "country": pa.array(countries, type=pa.string()),
        "signup_date": pa.array(signup_dates, type=pa.date32()),
        "loyalty_tier": pa.array(loyalty, type=pa.string()),
    })
    return table


def build_hosts(n_hosts: int, seed: int) -> pa.Table:
    fk = Faker()
    Faker.seed(seed + 1)
    rng = np.random.default_rng(seed + 1)
    ids = np.arange(1, n_hosts + 1)
    names = [fk.name() for _ in range(n_hosts)]
    countries = rng.choice(["US", "UK", "DE", "FR", "ES", "IT", "BR", "MX", "CA", "AU"], size=n_hosts)
    response_rate = np.clip(rng.normal(0.9, 0.12, size=n_hosts), 0.2, 1.0).round(2)
    is_superhost = rng.random(n_hosts) < 0.22
    table = pa.table({
        "host_id": pa.array(ids, type=pa.int32()),
        "host_name": pa.array(names, type=pa.string()),
        "country": pa.array(countries, type=pa.string()),
        "response_rate": pa.array(response_rate, type=pa.float32()),
        "is_superhost": pa.array(is_superhost, type=pa.bool_()),
    })
    return table


def build_properties(n_properties: int, n_hosts: int, seed: int) -> pa.Table:
    fk = Faker()
    Faker.seed(seed + 2)
    rng = np.random.default_rng(seed + 2)
    ids = np.arange(1, n_properties + 1)
    host_ids = rng.integers(1, n_hosts + 1, size=n_properties)
    ptypes = rng.choice(PROPERTY_TYPES, size=n_properties)
    cities = [fk.city() for _ in range(n_properties)]
    countries = rng.choice(["US", "UK", "DE", "FR", "ES", "IT", "BR", "MX", "CA", "AU"], size=n_properties)
    bedrooms = rng.integers(1, 6, size=n_properties)
    price = np.round(rng.gamma(4.0, 35.0, size=n_properties) + 30, 2)
    rating = np.clip(rng.normal(4.5, 0.4, size=n_properties), 2.5, 5.0).round(2)
    review_count = rng.integers(0, 800, size=n_properties)
    names = [f"{rng.choice(['Cozy','Modern','Sunny','Downtown','Seaside','Charming','Luxury','Rustic'])} " \
             f"{ptypes[i]} in {cities[i]}" for i in range(n_properties)]
    table = pa.table({
        "property_id": pa.array(ids, type=pa.int32()),
        "host_id": pa.array(host_ids, type=pa.int32()),
        "property_name": pa.array(names, type=pa.string()),
        "property_type": pa.array(ptypes, type=pa.string()),
        "city": pa.array(cities, type=pa.string()),
        "country": pa.array(countries, type=pa.string()),
        "bedrooms": pa.array(bedrooms, type=pa.int8()),
        "price_per_night": pa.array(price, type=pa.float32()),
        "rating": pa.array(rating, type=pa.float32()),
        "review_count": pa.array(review_count, type=pa.int32()),
    })
    return table


# --------------------------------------------------------------------------
# Fact table builders (chunked -> streamed to parquet)
# --------------------------------------------------------------------------

def generate_bookings(n_bookings: int, n_users: int, n_properties: int, properties_host_ids: np.ndarray,
                       seed: int, chunk_size: int, out_path: str):
    """Writes fact_bookings.parquet in chunks. Returns compact numpy arrays
    (booking_id, property_id, host_id, user_id, checkin_ordinal, checkout_ordinal)
    kept in memory for downstream conversation generation (cheap: a few
    columns of int32 for n_bookings rows, e.g. 150k rows ~= a few MB)."""
    rng = np.random.default_rng(seed + 3)
    writer = None
    base_date = dt.date(2024, 1, 1)

    keep_booking_id = np.empty(n_bookings, dtype=np.int32)
    keep_property_id = np.empty(n_bookings, dtype=np.int32)
    keep_host_id = np.empty(n_bookings, dtype=np.int32)
    keep_user_id = np.empty(n_bookings, dtype=np.int32)
    keep_checkin_ord = np.empty(n_bookings, dtype=np.int32)
    keep_checkout_ord = np.empty(n_bookings, dtype=np.int32)

    written = 0
    while written < n_bookings:
        n = min(chunk_size, n_bookings - written)
        booking_id = np.arange(written + 1, written + n + 1, dtype=np.int32)
        property_id = rng.integers(1, n_properties + 1, size=n).astype(np.int32)
        host_id = properties_host_ids[property_id - 1]
        user_id = rng.integers(1, n_users + 1, size=n).astype(np.int32)
        created_off = rng.integers(0, 500, size=n)
        checkin_off = created_off + rng.integers(1, 60, size=n)
        nights = rng.integers(1, 14, size=n)
        checkout_off = checkin_off + nights
        status = rng.choice(BOOKING_STATUSES, size=n, p=BOOKING_STATUS_WEIGHTS)
        channel = rng.choice(CHANNELS, size=n)
        price_per_night = rng.gamma(4.0, 35.0, size=n) + 30
        total_price = np.round(price_per_night * nights, 2)

        created_at = [base_date + dt.timedelta(days=int(o)) for o in created_off]
        checkin_date = [base_date + dt.timedelta(days=int(o)) for o in checkin_off]
        checkout_date = [base_date + dt.timedelta(days=int(o)) for o in checkout_off]

        table = pa.table({
            "booking_id": pa.array(booking_id),
            "property_id": pa.array(property_id),
            "host_id": pa.array(host_id.astype(np.int32)),
            "user_id": pa.array(user_id),
            "booking_created_at": pa.array(created_at, type=pa.date32()),
            "checkin_date": pa.array(checkin_date, type=pa.date32()),
            "checkout_date": pa.array(checkout_date, type=pa.date32()),
            "nights": pa.array(nights.astype(np.int16)),
            "status": pa.array(status),
            "channel": pa.array(channel),
            "total_price": pa.array(total_price.astype(np.float32)),
        })
        if writer is None:
            writer = pq.ParquetWriter(out_path, table.schema, compression="snappy")
        writer.write_table(table)

        keep_booking_id[written:written + n] = booking_id
        keep_property_id[written:written + n] = property_id
        keep_host_id[written:written + n] = host_id
        keep_user_id[written:written + n] = user_id
        keep_checkin_ord[written:written + n] = checkin_off
        keep_checkout_ord[written:written + n] = checkout_off

        written += n

    if writer is not None:
        writer.close()

    return {
        "booking_id": keep_booking_id,
        "property_id": keep_property_id,
        "host_id": keep_host_id,
        "user_id": keep_user_id,
        "checkin_ord": keep_checkin_ord,
        "checkout_ord": keep_checkout_ord,
    }


def generate_conversations_and_messages(bookings: dict, n_messages_target: int, property_names: list,
                                         support_agent_pool: int, seed: int, chunk_size: int,
                                         convo_out_path: str, msg_out_path: str, avg_msgs_per_convo: float = 9.0,
                                         generated_at: dt.datetime = None):
    """Streams dim_conversations.parquet and fact_chat_messages.parquet.
    Conversation count is derived from n_messages_target / avg_msgs_per_convo.
    Everything is generated and flushed in chunks of --chunk-size messages,
    so RAM use stays flat regardless of n_messages_target."""
    rng = np.random.default_rng(seed + 4)
    py_rng = random.Random(seed + 4)
    base_date = dt.date(2024, 1, 1)
    generated_at = generated_at or dt.datetime.now()

    n_bookings = len(bookings["booking_id"])
    n_convos = max(1, int(n_messages_target / avg_msgs_per_convo))
    # Sample which bookings get a support/host conversation (not every booking does)
    convo_booking_idx = rng.integers(0, n_bookings, size=n_convos)

    convo_writer = None
    msg_writer = None

    msg_id_counter = 1
    convo_chunk = {k: [] for k in [
        "conversation_id", "booking_id", "user_id", "host_id", "property_id",
        "channel", "intent_category", "status", "opened_at", "csat_score",
        "first_response_time_seconds", "resolution_time_seconds", "is_support",
    ]}
    msg_chunk = {k: [] for k in [
        "message_id", "conversation_id", "sender_type", "sender_id",
        "sent_at", "text", "intent_category", "sentiment_score",
        "sentiment_label", "language", "response_latency_seconds", "message_length",
    ]}

    def flush_convos():
        nonlocal convo_writer, convo_chunk
        if not convo_chunk["conversation_id"]:
            return
        table = pa.table({
            "conversation_id": pa.array(convo_chunk["conversation_id"], type=pa.int32()),
            "booking_id": pa.array(convo_chunk["booking_id"], type=pa.int32()),
            "user_id": pa.array(convo_chunk["user_id"], type=pa.int32()),
            "host_id": pa.array(convo_chunk["host_id"], type=pa.int32()),
            "property_id": pa.array(convo_chunk["property_id"], type=pa.int32()),
            "channel": pa.array(convo_chunk["channel"], type=pa.string()),
            "intent_category": pa.array(convo_chunk["intent_category"], type=pa.string()),
            "status": pa.array(convo_chunk["status"], type=pa.string()),
            "opened_at": pa.array(convo_chunk["opened_at"], type=pa.timestamp("s")),
            "csat_score": pa.array(convo_chunk["csat_score"], type=pa.int8()),
            "first_response_time_seconds": pa.array(convo_chunk["first_response_time_seconds"], type=pa.int32()),
            "resolution_time_seconds": pa.array(convo_chunk["resolution_time_seconds"], type=pa.int32()),
            "is_support": pa.array(convo_chunk["is_support"], type=pa.bool_()),
            "generated_at": pa.array(
                [generated_at] * len(convo_chunk["conversation_id"]), type=pa.timestamp("s")
            ),
        })
        if convo_writer is None:
            convo_writer = pq.ParquetWriter(convo_out_path, table.schema, compression="snappy")
        convo_writer.write_table(table)
        for k in convo_chunk:
            convo_chunk[k] = []

    def flush_msgs():
        nonlocal msg_writer, msg_chunk
        if not msg_chunk["message_id"]:
            return
        table = pa.table({
            "message_id": pa.array(msg_chunk["message_id"], type=pa.int64()),
            "conversation_id": pa.array(msg_chunk["conversation_id"], type=pa.int32()),
            "sender_type": pa.array(msg_chunk["sender_type"], type=pa.string()),
            "sender_id": pa.array(msg_chunk["sender_id"], type=pa.int32()),
            "sent_at": pa.array(msg_chunk["sent_at"], type=pa.timestamp("s")),
            "text": pa.array(msg_chunk["text"], type=pa.string()),
            "intent_category": pa.array(msg_chunk["intent_category"], type=pa.string()),
            "sentiment_score": pa.array(msg_chunk["sentiment_score"], type=pa.float32()),
            "sentiment_label": pa.array(msg_chunk["sentiment_label"], type=pa.string()),
            "language": pa.array(msg_chunk["language"], type=pa.string()),
            "response_latency_seconds": pa.array(msg_chunk["response_latency_seconds"], type=pa.int32()),
            "message_length": pa.array(msg_chunk["message_length"], type=pa.int16()),
            "generated_at": pa.array(
                [generated_at] * len(msg_chunk["message_id"]), type=pa.timestamp("s")
            ),
        })
        nonlocal msg_writer
        if msg_writer is None:
            msg_writer = pq.ParquetWriter(msg_out_path, table.schema, compression="snappy")
        msg_writer.write_table(table)
        for k in msg_chunk:
            msg_chunk[k] = []

    total_msgs_written = 0
    convo_id = 0
    for bidx in convo_booking_idx:
        convo_id += 1
        booking_id = int(bookings["booking_id"][bidx])
        property_id = int(bookings["property_id"][bidx])
        host_id = int(bookings["host_id"][bidx])
        user_id = int(bookings["user_id"][bidx])
        checkin_ord = int(bookings["checkin_ord"][bidx])

        intent = rng.choice(INTENTS, p=INTENT_WEIGHTS)
        is_support = intent in (
            "refund_request", "payment_issue", "cancellation_request",
            "host_unresponsive", "lost_item",
        ) or py_rng.random() < 0.3
        channel = rng.choice(CHAT_CHANNELS, p=[0.55, 0.15, 0.2, 0.1])
        n_msgs = max(1, int(rng.poisson(avg_msgs_per_convo)))

        # conversation starts a few days before checkin (pre-booking Qs) or
        # during/after the stay (issues/feedback), depending on intent.
        if intent in ("pre_booking_question",):
            open_off_days = checkin_ord - rng.integers(1, 20)
        elif intent in ("positive_feedback",):
            open_off_days = checkin_ord + rng.integers(1, 5) + int(bookings["checkout_ord"][bidx] - checkin_ord)
        else:
            open_off_days = checkin_ord + rng.integers(-1, 4)
        open_off_days = max(0, open_off_days)
        opened_at = dt.datetime.combine(base_date, dt.time(0, 0)) + dt.timedelta(
            days=int(open_off_days), seconds=int(rng.integers(0, 86400))
        )

        first_response = int(np.clip(rng.exponential(600), 30, 7200))  # seconds
        resolution = int(first_response + np.clip(rng.exponential(1800), 60, 86400))
        status = py_rng.choices(CONVO_STATUSES, weights=[0.75, 0.15, 0.10])[0]

        # Decide who sends each message *before* picking status/CSAT, so both
        # stay consistent with how the conversation actually ends -- without
        # this, status and CSAT are drawn independently of the transcript and
        # you get things like a "resolved" thread with a perfect CSAT whose
        # last message is an unanswered guest question.
        senders = []
        for i in range(n_msgs):
            if i == 0:
                senders.append(("guest", user_id))
            elif py_rng.random() < 0.5:
                senders.append(("guest", user_id))
            elif is_support:
                sender_type = py_rng.choices(["support_agent", "bot"], weights=[0.8, 0.2])[0]
                senders.append((sender_type, py_rng.randint(1, support_agent_pool) if sender_type == "support_agent" else 0))
            else:
                senders.append(("host", host_id))
        ends_unanswered = senders[-1][0] == "guest"

        # A thread can't be "resolved" if it ends without ever getting a
        # reply (including the single-message "opened, never answered" case).
        if status == "resolved" and (n_msgs == 1 or ends_unanswered):
            status = "open"

        # CSAT correlates with resolution status, how fast the first
        # response was (slow first response measurably drags CSAT down), and
        # whether the guest's last message ever got a reply at all -- these
        # are the kind of relationships you'd want to mine from this dataset
        # when optimizing the chat experience.
        base_csat = 4.4 if status == "resolved" else 3.0
        speed_penalty = min(1.5, first_response / 3600.0)  # up to -1.5 for slow (~1hr+) first response
        unanswered_penalty = 1.8 if (n_msgs == 1 or ends_unanswered) else 0.0
        csat = int(np.clip(rng.normal(base_csat - speed_penalty - unanswered_penalty, 0.7), 1, 5))

        p_name = property_names[property_id - 1]

        convo_chunk["conversation_id"].append(convo_id)
        convo_chunk["booking_id"].append(booking_id)
        convo_chunk["user_id"].append(user_id)
        convo_chunk["host_id"].append(host_id)
        convo_chunk["property_id"].append(property_id)
        convo_chunk["channel"].append(channel)
        convo_chunk["intent_category"].append(intent)
        convo_chunk["status"].append(status)
        convo_chunk["opened_at"].append(opened_at)
        convo_chunk["csat_score"].append(csat)
        convo_chunk["first_response_time_seconds"].append(first_response)
        convo_chunk["resolution_time_seconds"].append(resolution)
        convo_chunk["is_support"].append(bool(is_support))

        template_sides = TEMPLATES[intent]
        checkin_date = base_date + dt.timedelta(days=checkin_ord)
        checkout_date = base_date + dt.timedelta(days=int(bookings["checkout_ord"][bidx]))

        t = opened_at
        for i in range(n_msgs):
            if total_msgs_written >= n_messages_target:
                break
            sender_type, sender_id = senders[i]

            latency = 0 if i == 0 else int(np.clip(rng.exponential(300), 5, 14400))
            t = t + dt.timedelta(seconds=latency)

            side = "guest" if sender_type == "guest" else "other"
            template = py_rng.choice(template_sides[side])
            text = fill_template(py_rng, template, p_name, checkin_date, checkout_date)

            sentiment = float(np.clip(rng.normal(
                0.3 if intent == "positive_feedback" else (-0.4 if "complaint" in intent or intent in
                    ("refund_request", "payment_issue", "checkin_issue", "host_unresponsive") else 0.0),
                0.3), -1.0, 1.0))
            sentiment_label = "positive" if sentiment > 0.2 else ("negative" if sentiment < -0.2 else "neutral")

            msg_chunk["message_id"].append(msg_id_counter)
            msg_chunk["conversation_id"].append(convo_id)
            msg_chunk["sender_type"].append(sender_type)
            msg_chunk["sender_id"].append(sender_id)
            msg_chunk["sent_at"].append(t)
            msg_chunk["text"].append(text)
            msg_chunk["intent_category"].append(intent)
            msg_chunk["sentiment_score"].append(sentiment)
            msg_chunk["sentiment_label"].append(sentiment_label)
            msg_chunk["language"].append(py_rng.choices(LANGUAGES, weights=[0.7, 0.08, 0.06, 0.06, 0.06, 0.04])[0])
            msg_chunk["response_latency_seconds"].append(latency)
            msg_chunk["message_length"].append(len(text))

            msg_id_counter += 1
            total_msgs_written += 1

            if len(msg_chunk["message_id"]) >= chunk_size:
                flush_msgs()

        if len(convo_chunk["conversation_id"]) >= chunk_size:
            flush_convos()

        if total_msgs_written >= n_messages_target:
            break

    flush_convos()
    flush_msgs()
    if convo_writer is not None:
        convo_writer.close()
    if msg_writer is not None:
        msg_writer.close()

    return total_msgs_written, convo_id


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="./data")
    ap.add_argument("--n-users", type=int, default=50_000)
    ap.add_argument("--n-hosts", type=int, default=4_000)
    ap.add_argument("--n-properties", type=int, default=10_000)
    ap.add_argument("--n-bookings", type=int, default=250_000)
    ap.add_argument("--n-messages", type=int, default=1_000_000, help="target number of chat messages")
    ap.add_argument("--n-support-agents", type=int, default=60)
    ap.add_argument("--avg-msgs-per-convo", type=float, default=9.0)
    ap.add_argument("--chunk-size", type=int, default=50_000, help="rows per parquet write batch")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    print(f"[1/5] Building dim_users ({args.n_users:,})...")
    users = build_users(args.n_users, args.seed)
    pq.write_table(users, os.path.join(args.out_dir, "dim_users.parquet"), compression="snappy")

    print(f"[2/5] Building dim_hosts ({args.n_hosts:,})...")
    hosts = build_hosts(args.n_hosts, args.seed)
    pq.write_table(hosts, os.path.join(args.out_dir, "dim_hosts.parquet"), compression="snappy")

    print(f"[3/5] Building dim_properties ({args.n_properties:,})...")
    properties = build_properties(args.n_properties, args.n_hosts, args.seed)
    pq.write_table(properties, os.path.join(args.out_dir, "dim_properties.parquet"), compression="snappy")
    properties_host_ids = properties.column("host_id").to_numpy()
    property_names = properties.column("property_name").to_pylist()

    print(f"[4/5] Streaming fact_bookings ({args.n_bookings:,}) in chunks of {args.chunk_size:,}...")
    bookings = generate_bookings(
        args.n_bookings, args.n_users, args.n_properties, properties_host_ids,
        args.seed, args.chunk_size, os.path.join(args.out_dir, "fact_bookings.parquet"),
    )

    print(f"[5/5] Streaming dim_conversations + fact_chat_messages (target {args.n_messages:,} messages)...")
    n_msgs, n_convos = generate_conversations_and_messages(
        bookings, args.n_messages, property_names, args.n_support_agents, args.seed, args.chunk_size,
        os.path.join(args.out_dir, "dim_conversations.parquet"),
        os.path.join(args.out_dir, "fact_chat_messages.parquet"),
        avg_msgs_per_convo=args.avg_msgs_per_convo,
        generated_at=dt.datetime.now().replace(microsecond=0),
    )

    print(f"\nDone. Wrote {n_convos:,} conversations and {n_msgs:,} chat messages to {args.out_dir}/")
    for f in sorted(os.listdir(args.out_dir)):
        path = os.path.join(args.out_dir, f)
        size_mb = os.path.getsize(path) / 1e6
        print(f"  {f:30s} {size_mb:8.2f} MB")


if __name__ == "__main__":
    main()
