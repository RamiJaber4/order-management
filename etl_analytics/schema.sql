-- Explicit bootstrap for ETL schema version 1, not an API startup migration.
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE TABLE IF NOT EXISTS analytics.etl_schema_version (
    version integer PRIMARY KEY CHECK (version = 1)
);
INSERT INTO analytics.etl_schema_version VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS analytics.etl_runs (
    run_id uuid PRIMARY KEY,
    snapshot_started_at timestamptz NOT NULL,
    published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    source_calendar text NOT NULL,
    status text NOT NULL CHECK (status IN ('complete','incomplete')),
    totals jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS analytics.daily_order_metrics (
    order_day date PRIMARY KEY,
    order_count bigint NOT NULL CHECK (order_count >= 0),
    orders_with_items bigint NOT NULL CHECK (orders_with_items >= 0),
    orders_without_items bigint NOT NULL CHECK (orders_without_items >= 0),
    item_count bigint NOT NULL CHECK (item_count >= 0),
    units bigint NOT NULL CHECK (units >= 0),
    recorded_item_value_ils numeric(30,0) NOT NULL CHECK (recorded_item_value_ils >= 0),
    run_id uuid NOT NULL REFERENCES analytics.etl_runs(run_id),
    CHECK (order_count = orders_with_items + orders_without_items)
);
CREATE TABLE IF NOT EXISTS analytics.etl_current_run (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    run_id uuid NOT NULL REFERENCES analytics.etl_runs(run_id)
);
