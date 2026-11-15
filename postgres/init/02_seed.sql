-- Seed data: ~1000 products, ~500 customers and a week of order history.
SELECT setseed(0.42);

-- Customers ------------------------------------------------------------------
INSERT INTO customers (email, full_name, city, created_at)
SELECT format('customer%s@example.com', g),
       (ARRAY['Olena','Andrii','Iryna','Taras','Maria','Dmytro','Sofiia','Oleh','Kateryna','Bohdan'])[1 + (g % 10)]
         || ' ' ||
       (ARRAY['Shevchenko','Kovalenko','Bondarenko','Tkachenko','Kravchenko','Melnyk','Boyko','Moroz'])[1 + (g % 8)],
       (ARRAY['Kyiv','Lviv','Kharkiv','Odesa','Dnipro','Vinnytsia','Poltava','Chernihiv'])[1 + (g % 8)],
       now() - (random() * interval '365 days')
FROM generate_series(1, 500) AS g;

-- Products -------------------------------------------------------------------
WITH cat AS (
    SELECT * FROM (VALUES
        (1, 'electronics', ARRAY['Smartphone','Laptop','Headphones','Monitor','Keyboard','Mouse','Tablet','Camera']),
        (2, 'books',       ARRAY['Novel','Textbook','Cookbook','Comic','Guide','Atlas','Dictionary','Biography']),
        (3, 'home',        ARRAY['Lamp','Chair','Table','Pillow','Blanket','Vase','Mirror','Shelf']),
        (4, 'sports',      ARRAY['Ball','Racket','Bike','Helmet','Mat','Dumbbell','Tent','Backpack']),
        (5, 'clothing',    ARRAY['Jacket','Shirt','Jeans','Sneakers','Hat','Scarf','Dress','Sweater']),
        (6, 'toys',        ARRAY['Puzzle','Robot','Doll','Blocks','Kite','Car','Train','Board game']),
        (7, 'grocery',     ARRAY['Coffee','Tea','Honey','Chocolate','Olive oil','Pasta','Rice','Spices']),
        (8, 'beauty',      ARRAY['Cream','Shampoo','Perfume','Soap','Lotion','Brush','Serum','Mask'])
    ) AS t(n, category, nouns)
)
INSERT INTO products (name, category, price, stock, updated_at)
SELECT (ARRAY['Smart','Classic','Pro','Eco','Ultra','Mini','Deluxe','Basic','Retro','Nova'])[1 + (g % 10)]
         || ' ' || cat.nouns[1 + ((g / 8) % 8)] || ' ' || g,
       cat.category,
       round((5 + random() * 995)::numeric, 2),
       (random() * 200)::int,
       now() - (random() * interval '30 days')
FROM generate_series(1, 1000) AS g
JOIN cat ON cat.n = 1 + (g % 8);

-- Order history (last 7 days) -----------------------------------------------
INSERT INTO orders (customer_id, status, created_at, updated_at)
SELECT 1 + (random() * 499)::int,
       CASE WHEN random() < 0.9 THEN 'paid' ELSE 'failed' END,
       ts, ts + interval '2 seconds'
FROM (SELECT now() - (random() * interval '7 days') AS ts
      FROM generate_series(1, 3000)) AS s;

INSERT INTO order_items (order_id, product_id, quantity, unit_price)
SELECT o.id, p.id, 1 + (random() * 3)::int, p.price
FROM orders o
CROSS JOIN generate_series(1, 3) AS n
CROSS JOIN LATERAL (
    SELECT id, price FROM products
    WHERE id = 1 + ((o.id * 7919 + n * 104729) % 1000)
) AS p
WHERE n <= 1 + (o.id % 3);

UPDATE orders o
SET total = s.total,
    paid_at = CASE WHEN o.status = 'paid' THEN o.updated_at END
FROM (SELECT order_id, sum(quantity * unit_price) AS total
      FROM order_items GROUP BY order_id) AS s
WHERE s.order_id = o.id;

INSERT INTO payments (order_id, status, amount, attempts, created_at, updated_at)
SELECT id,
       CASE WHEN status = 'paid' THEN 'succeeded' ELSE 'failed' END,
       total, 1, created_at, updated_at
FROM orders;

ANALYZE;
