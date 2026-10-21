-- ============================================================================
-- Inventory Intelligence Dashboard (IID) — PostgreSQL Schema
-- ============================================================================
-- This is the production DDL. The application's SQLAlchemy models
-- (backend/models/models.py) mirror this schema and will auto-create it
-- on startup for convenience, but running this file directly against a
-- fresh PostgreSQL database is the recommended path for production.
--
-- Usage:
--   createdb iid_db
--   psql -d iid_db -f schema.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS upload_batches (
    id              SERIAL PRIMARY KEY,
    filename        VARCHAR(255) NOT NULL,
    row_count       INTEGER DEFAULT 0,
    uploaded_at     TIMESTAMPTZ DEFAULT now(),
    status          VARCHAR(50) DEFAULT 'processed'
);

CREATE TABLE IF NOT EXISTS suppliers (
    id                      SERIAL PRIMARY KEY,
    name                    VARCHAR(255) UNIQUE NOT NULL,
    on_time_delivery_rate   DOUBLE PRECISION DEFAULT 0.0,
    quality_score           DOUBLE PRECISION DEFAULT 0.0,
    avg_lead_time_days      DOUBLE PRECISION DEFAULT 0.0,
    total_orders            INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_suppliers_name ON suppliers(name);

CREATE TABLE IF NOT EXISTS warehouses (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(255) UNIQUE NOT NULL,
    capacity_units  DOUBLE PRECISION DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_warehouses_name ON warehouses(name);

CREATE TABLE IF NOT EXISTS inventory_items (
    id                  SERIAL PRIMARY KEY,
    batch_id            INTEGER REFERENCES upload_batches(id) ON DELETE CASCADE,

    sku                 VARCHAR(100) NOT NULL,
    product_name        VARCHAR(255) NOT NULL,
    category            VARCHAR(150),

    quantity_on_hand    DOUBLE PRECISION DEFAULT 0.0,
    reorder_point       DOUBLE PRECISION DEFAULT 0.0,
    safety_stock        DOUBLE PRECISION DEFAULT 0.0,
    max_stock_level     DOUBLE PRECISION DEFAULT 0.0,

    unit_cost           DOUBLE PRECISION DEFAULT 0.0,
    unit_price          DOUBLE PRECISION DEFAULT 0.0,

    lead_time_days      DOUBLE PRECISION DEFAULT 0.0,
    daily_avg_demand    DOUBLE PRECISION DEFAULT 0.0,

    expiry_date         DATE,
    last_restock_date   DATE,

    supplier_id         INTEGER REFERENCES suppliers(id),
    warehouse_id        INTEGER REFERENCES warehouses(id),

    status              VARCHAR(50) DEFAULT 'normal'
);
CREATE INDEX IF NOT EXISTS idx_items_sku ON inventory_items(sku);
CREATE INDEX IF NOT EXISTS idx_items_category ON inventory_items(category);
CREATE INDEX IF NOT EXISTS idx_items_batch ON inventory_items(batch_id);

CREATE TABLE IF NOT EXISTS recommendations (
    id                  SERIAL PRIMARY KEY,
    item_id             INTEGER REFERENCES inventory_items(id) ON DELETE CASCADE,
    batch_id            INTEGER REFERENCES upload_batches(id) ON DELETE CASCADE,

    title               VARCHAR(255) NOT NULL,
    category            VARCHAR(100),
    priority            VARCHAR(50),
    reasoning           TEXT,
    suggested_action    TEXT,
    impact_score        DOUBLE PRECISION DEFAULT 0.0,

    created_at          TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_recommendations_item ON recommendations(item_id);

-- ============================================================================
-- Helpful views for BI tools (optional, not required by the API)
-- ============================================================================

CREATE OR REPLACE VIEW v_inventory_status AS
SELECT
    i.id, i.sku, i.product_name, i.category,
    i.quantity_on_hand, i.reorder_point, i.safety_stock, i.max_stock_level,
    i.unit_cost, i.unit_price,
    (i.quantity_on_hand * i.unit_cost) AS stock_value,
    s.name AS supplier_name,
    w.name AS warehouse_name,
    i.expiry_date,
    CASE
        WHEN i.expiry_date IS NOT NULL AND i.expiry_date - CURRENT_DATE <= 30 THEN 'expiring'
        WHEN i.quantity_on_hand <= i.safety_stock THEN 'critical'
        WHEN i.quantity_on_hand <= i.reorder_point THEN 'low'
        WHEN i.max_stock_level > 0 AND i.quantity_on_hand > i.max_stock_level THEN 'overstock'
        ELSE 'normal'
    END AS computed_status
FROM inventory_items i
LEFT JOIN suppliers s ON s.id = i.supplier_id
LEFT JOIN warehouses w ON w.id = i.warehouse_id;
