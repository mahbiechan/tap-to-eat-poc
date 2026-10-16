-- Tap-To-Eat PoC schema (PostgreSQL + pgvector)
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE menu_items (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL,
    category    TEXT NOT NULL,
    cuisine     TEXT NOT NULL,
    price       NUMERIC(10,2) NOT NULL,
    available   BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE orders (
    id           SERIAL PRIMARY KEY,
    customer_id  INT NOT NULL,
    total        NUMERIC(10,2) NOT NULL,
    status       TEXT NOT NULL DEFAULT 'received',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at TIMESTAMPTZ
);

CREATE TABLE order_items (
    id           SERIAL PRIMARY KEY,
    order_id     INT NOT NULL REFERENCES orders(id),
    menu_item_id INT NOT NULL REFERENCES menu_items(id),
    quantity     INT NOT NULL CHECK (quantity > 0),
    unit_price   NUMERIC(10,2) NOT NULL
);

-- One 384-dimension vector per menu item (all-MiniLM-L6-v2)
CREATE TABLE menu_item_embeddings (
    menu_item_id INT PRIMARY KEY REFERENCES menu_items(id),
    embedding    vector(384) NOT NULL
);
CREATE INDEX menu_item_embeddings_hnsw
    ON menu_item_embeddings USING hnsw (embedding vector_cosine_ops);

INSERT INTO menu_items (name, description, category, cuisine, price) VALUES
('Chicken Burger Deluxe',   'Crispy chicken fillet burger with lettuce and mayo',        'burgers',  'fast food', 189.00),
('Spicy Chicken Sandwich',  'Fried chicken burger with chili mayo and pickles',          'burgers',  'fast food', 199.00),
('Classic Cheeseburger',    'Beef patty burger with melted cheese and onions',           'burgers',  'fast food', 175.00),
('Fried Chicken Bucket',    'Eight pieces of crispy fried chicken for sharing',          'chicken',  'fast food', 449.00),
('Spaghetti Bolognese',     'Spaghetti pasta with meat tomato sauce and parmesan',       'pasta',    'italian',   229.00),
('Creamy Carbonara',        'Pasta with creamy white sauce, bacon, and mushrooms',       'pasta',    'italian',   239.00),
('Pepperoni Pizza',         'Cheesy pizza topped with pepperoni slices',                 'pizza',    'italian',   349.00),
('Garden Veggie Pizza',     'Pizza topped with bell peppers, olives, and mushrooms',     'pizza',    'italian',   329.00),
('Chicken Adobo Rice Meal', 'Chicken adobo in soy vinegar sauce served with garlic rice','rice meal','filipino',  159.00),
('Pork Sisig',              'Sizzling chopped pork with onions and chili, served with rice','rice meal','filipino',189.00),
('Halo-Halo',               'Shaved ice dessert with sweet beans, fruits, and leche flan','dessert', 'filipino',  129.00),
('Chocolate Sundae',        'Vanilla soft serve ice cream with chocolate syrup',         'dessert',  'fast food',  59.00),
('Iced Tea',                'Cold sweet lemon iced tea',                                 'drinks',   'fast food',  55.00),
('Cola',                    'Chilled carbonated cola drink',                             'drinks',   'fast food',  50.00);
