#!/usr/bin/env bash
# Rebuilds the warehouse from the current data/*.parquet, reports source
# freshness, and refreshes the dashboard's embedded JSON (in the separate
# booking_project_dashboard repo, expected as a sibling directory unless
# overridden) from the resulting marts. Does NOT regenerate synthetic data
# -- run generate_hotel_chat_data.py yourself first if you want new data.
#
# Usage: scripts/refresh_pipeline.sh [--dashboard-dir PATH]
# Intended to be run from the project root, and is what the weekly cron job
# (see README.md) invokes.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

source .venv/bin/activate

echo "[1/3] dbt build"
dbt build

echo "[2/3] dbt source freshness"
# Non-blocking: a stale source shouldn't stop the dashboard from refreshing
# with whatever data is currently in the warehouse, just flag it.
dbt source freshness || echo "WARNING: source freshness check failed or is stale -- see output above."

echo "[3/3] refresh dashboard JSON"
python3 scripts/export_dashboard_data.py "$@"

echo "Refresh complete: $(date)"
