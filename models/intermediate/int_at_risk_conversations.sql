{{ config(
    materialized = 'table',
    tags = ['intermediate']
) }}

with at_risk_bookings as (

    select
        booking_id,
        cancellation_risk_score,
        risk_percentile
    from {{ ref('int_booking_cancellation_risk') }}
    where is_top_risk_decile

),

conversations as (

    select *
    from {{ ref('stg_dim_conversations') }}

)

select
    c.*,
    b.cancellation_risk_score,
    b.risk_percentile
from conversations c
inner join at_risk_bookings b using (booking_id)
