{{ config(
    materialized = 'view',
    tags = ['staging']
) }}

select * from {{ source('raw', 'fact_chat_messages') }}
