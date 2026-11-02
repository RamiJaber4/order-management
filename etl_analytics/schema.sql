-- Explicit bootstrap for ETL schema version 2, not an API startup migration.
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE TABLE IF NOT EXISTS analytics.etl_schema_version (
    version integer PRIMARY KEY CHECK (version = 2)
);
INSERT INTO analytics.etl_schema_version VALUES (2) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS analytics.etl_runs (
    run_id uuid PRIMARY KEY,
    snapshot_started_at timestamptz NOT NULL,
    published_at timestamptz DEFAULT clock_timestamp(),
    source_calendar text NOT NULL,
    status text NOT NULL CHECK (status IN ('running','complete','failed','incomplete')),
    totals jsonb NOT NULL,
    pipeline_name text NOT NULL DEFAULT 'daily_orders',
    mode text NOT NULL DEFAULT 'full' CHECK (mode IN ('full','incremental')),
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    duration_seconds numeric(18,6),
    rows_read bigint NOT NULL DEFAULT 0,
    rows_inserted bigint NOT NULL DEFAULT 0,
    rows_updated bigint NOT NULL DEFAULT 0,
    rows_unchanged bigint NOT NULL DEFAULT 0,
    rows_rejected bigint NOT NULL DEFAULT 0,
    watermark_before timestamptz,
    watermark_after timestamptz,
    lookback_seconds integer NOT NULL DEFAULT 300 CHECK (lookback_seconds>=0),
    error_code text
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
CREATE TABLE IF NOT EXISTS analytics.fact_order_sales (
    order_id integer NOT NULL,
    product_id integer NOT NULL,
    customer_id integer,
    order_date timestamp NOT NULL,
    quantity bigint NOT NULL CHECK (quantity > 0),
    unit_price bigint NOT NULL CHECK (unit_price >= 0),
    line_value numeric(30,0) NOT NULL CHECK (line_value >= 0),
    order_updated_at timestamptz NOT NULL,
    item_updated_at timestamptz NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    run_id uuid NOT NULL REFERENCES analytics.etl_runs(run_id),
    PRIMARY KEY (order_id,product_id)
);
CREATE TABLE IF NOT EXISTS analytics.etl_checkpoints (
    pipeline_name text PRIMARY KEY,
    source_key text NOT NULL,
    watermark timestamptz NOT NULL,
    run_id uuid NOT NULL REFERENCES analytics.etl_runs(run_id),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE OR REPLACE VIEW analytics.sales_daily_metrics AS
SELECT order_date::date AS order_day,count(DISTINCT order_id) AS order_count,
       count(*) AS item_count,sum(quantity) AS units,sum(line_value) AS recorded_item_value_ils
FROM analytics.fact_order_sales GROUP BY order_date::date;
