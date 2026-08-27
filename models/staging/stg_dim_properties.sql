{{ config(
    materialized = 'view',
    tags = ['staging']
) }}

select * from {{ source('raw', 'dim_properties') }}
