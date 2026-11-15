-- Schema of the online shop order-processing service.
-- Executed once by the postgres image entrypoint on an empty data directory.

CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE customers (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email       text        NOT NULL UNIQUE,
    full_name   text        NOT NULL,
    city        text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE products (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        text          NOT NULL,
    category    text          NOT NULL,
    price       numeric(10,2) NOT NULL CHECK (price > 0),
    stock       integer       NOT NULL CHECK (stock >= 0),
    updated_at  timestamptz   NOT NULL DEFAULT now()
);
CREATE INDEX products_category_price_idx ON products (category, price);
-- Trigram index makes ILIKE '%term%' catalog search index-assisted
CREATE INDEX products_name_trgm_idx ON products USING gin (name gin_trgm_ops);
CREATE INDEX products_low_stock_idx ON products (stock) WHERE stock < 20;

CREATE TABLE orders (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id  bigint        NOT NULL REFERENCES customers (id),
    status       text          NOT NULL DEFAULT 'created'
                 CHECK (status IN ('created', 'paid', 'failed', 'archived')),
    total        numeric(12,2) NOT NULL DEFAULT 0 CHECK (total >= 0),
    created_at   timestamptz   NOT NULL DEFAULT now(),
    updated_at   timestamptz   NOT NULL DEFAULT now(),
    paid_at      timestamptz
);
CREATE INDEX orders_customer_created_idx ON orders (customer_id, created_at DESC);
CREATE INDEX orders_status_created_idx ON orders (status, created_at);

CREATE TABLE order_items (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id    bigint        NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    product_id  bigint        NOT NULL REFERENCES products (id),
    quantity    integer       NOT NULL CHECK (quantity > 0),
    unit_price  numeric(10,2) NOT NULL CHECK (unit_price > 0)
);
CREATE INDEX order_items_order_idx ON order_items (order_id);
CREATE INDEX order_items_product_idx ON order_items (product_id);

CREATE TABLE payments (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id    bigint        NOT NULL UNIQUE REFERENCES orders (id) ON DELETE CASCADE,
    status      text          NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'succeeded', 'failed')),
    amount      numeric(12,2) NOT NULL CHECK (amount >= 0),
    attempts    integer       NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    created_at  timestamptz   NOT NULL DEFAULT now(),
    updated_at  timestamptz   NOT NULL DEFAULT now()
);
CREATE INDEX payments_status_idx ON payments (status);
