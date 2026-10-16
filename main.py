"""Tap-To-Eat PoC: FastAPI + PostgreSQL + Redis + pgvector.

Scenario 3 (section 4.5.4): save an order in PostgreSQL (ACID), keep the live
status in Redis (BASE), then return similar items using pgvector.
"""
import json
import os
import time
from contextlib import asynccontextmanager
from typing import List

import redis
from fastapi import FastAPI, HTTPException, Query
from fastembed import TextEmbedding
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tap:tap@postgres:5432/taptoeat")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"  # 384 dimensions
STATUSES = ["received", "preparing", "picked_up", "on_the_way", "delivered"]

pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5, open=False)
r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
model = None


def ms(start: float) -> float:
    """Milliseconds elapsed since start."""
    return round((time.perf_counter() - start) * 1000, 2)


def vec_literal(vec) -> str:
    """Convert an embedding to pgvector text format: [0.1,0.2,...]"""
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"


def build_embeddings():
    """Embed every menu item that has no vector yet (4.3.1)."""
    with pool.connection() as conn:
        rows = conn.execute(
            """SELECT m.id, m.name, m.description, m.category, m.cuisine
               FROM menu_items m
               LEFT JOIN menu_item_embeddings e ON e.menu_item_id = m.id
               WHERE e.menu_item_id IS NULL"""
        ).fetchall()
        if not rows:
            return
        texts = [f"{n}. {d}. Category: {c}. Cuisine: {cu}." for _, n, d, c, cu in rows]
        embeddings = list(model.embed(texts))
        for row, emb in zip(rows, embeddings):
            conn.execute(
                "INSERT INTO menu_item_embeddings (menu_item_id, embedding) VALUES (%s, %s::vector)",
                (row[0], vec_literal(emb)),
            )
    print(f"Created embeddings for {len(rows)} menu items")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global model
    pool.open(wait=True, timeout=60)
    model = TextEmbedding(MODEL_NAME, cache_dir="/app/model_cache")
    build_embeddings()
    yield
    pool.close()


app = FastAPI(title="Tap-To-Eat PoC", lifespan=lifespan)


class OrderItem(BaseModel):
    menu_item_id: int
    quantity: int = Field(default=1, ge=1, le=20)


class OrderIn(BaseModel):
    customer_id: int
    items: List[OrderItem] = Field(min_length=1)


class StatusIn(BaseModel):
    status: str


class LocationIn(BaseModel):
    lat: float
    lng: float


@app.get("/health")
def health():
    return {"ok": True}


# ---- Menu browsing: Redis cache in front of PostgreSQL (R4) ----
@app.get("/menu")
def get_menu():
    key = "cache:menu:popular"
    t = time.perf_counter()
    cached = r.get(key)
    if cached:
        return {"source": "redis_cache", "timing_ms": ms(t), "items": json.loads(cached)}
    t = time.perf_counter()
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, name, category, cuisine, price FROM menu_items WHERE available ORDER BY id"
        ).fetchall()
    items = [
        {"id": i, "name": n, "category": c, "cuisine": cu, "price": float(p)}
        for i, n, c, cu, p in rows
    ]
    elapsed = ms(t)
    r.set(key, json.dumps(items), ex=60)
    return {"source": "postgresql", "timing_ms": elapsed, "items": items}


# ---- Place an order: PostgreSQL first (ACID), then Redis (BASE) ----
@app.post("/orders")
def create_order(order: OrderIn):
    t = time.perf_counter()
    with pool.connection() as conn:  # one transaction, committed on exit
        ids = [i.menu_item_id for i in order.items]
        prices = dict(
            conn.execute(
                "SELECT id, price FROM menu_items WHERE id = ANY(%s) AND available", (ids,)
            ).fetchall()
        )
        missing = [i for i in ids if i not in prices]
        if missing:
            raise HTTPException(400, f"Unknown or unavailable menu items: {missing}")
        total = sum(prices[i.menu_item_id] * i.quantity for i in order.items)
        order_id = conn.execute(
            "INSERT INTO orders (customer_id, total, status) VALUES (%s, %s, 'received') RETURNING id",
            (order.customer_id, total),
        ).fetchone()[0]
        for it in order.items:
            conn.execute(
                "INSERT INTO order_items (order_id, menu_item_id, quantity, unit_price) VALUES (%s, %s, %s, %s)",
                (order_id, it.menu_item_id, it.quantity, prices[it.menu_item_id]),
            )
    pg_ms = ms(t)

    # Only after the PostgreSQL commit succeeds do we write the live status.
    t = time.perf_counter()
    try:
        r.set(f"order:{order_id}:status", "received")
        redis_ms = ms(t)
    except redis.RedisError:
        redis_ms = None  # order is safe in PostgreSQL; status can be rebuilt

    return {
        "order_id": order_id,
        "total": float(total),
        "status": "received",
        "timing_ms": {"postgres_insert": pg_ms, "redis_set": redis_ms},
    }


# ---- Live status: read from Redis (R2) ----
@app.get("/orders/{order_id}/status")
def get_status(order_id: int, source: str = Query("redis", pattern="^(redis|postgres)$")):
    key = f"order:{order_id}:status"
    t = time.perf_counter()
    status = None
    if source == "redis":
        status = r.get(key)
    if status is None:
        with pool.connection() as conn:
            row = conn.execute("SELECT status FROM orders WHERE id = %s", (order_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Order not found")
        status = row[0]
        if source == "redis":  # Redis had lost it: rebuild from PostgreSQL
            r.set(key, status)
            source = "postgres_rebuild"
    return {"order_id": order_id, "status": status, "source": source, "timing_ms": ms(t)}


@app.patch("/orders/{order_id}/status")
def update_status(order_id: int, body: StatusIn):
    if body.status not in STATUSES:
        raise HTTPException(400, f"status must be one of {STATUSES}")
    key = f"order:{order_id}:status"
    t = time.perf_counter()
    if not r.exists(key):
        with pool.connection() as conn:
            if conn.execute("SELECT 1 FROM orders WHERE id = %s", (order_id,)).fetchone() is None:
                raise HTTPException(404, "Order not found")
    r.set(key, body.status)
    redis_ms = ms(t)
    pg_ms = None
    if body.status == "delivered":  # final status goes back to PostgreSQL
        t = time.perf_counter()
        with pool.connection() as conn:
            conn.execute(
                "UPDATE orders SET status = 'delivered', delivered_at = now() WHERE id = %s",
                (order_id,),
            )
        pg_ms = ms(t)
    return {
        "order_id": order_id,
        "status": body.status,
        "timing_ms": {"redis_set": redis_ms, "postgres_update": pg_ms},
    }


# ---- Courier location: Redis only (R3) ----
@app.put("/couriers/{courier_id}/location")
def put_location(courier_id: int, loc: LocationIn):
    t = time.perf_counter()
    r.set(
        f"courier:{courier_id}:location",
        json.dumps({"lat": loc.lat, "lng": loc.lng, "updated_at": time.time()}),
        ex=300,
    )
    return {"courier_id": courier_id, "timing_ms": ms(t)}


# ---- Recommendations: pgvector cosine similarity (R5) ----
@app.get("/recommendations")
def recommendations(item_id: int, limit: int = Query(5, ge=1, le=10)):
    t = time.perf_counter()
    with pool.connection() as conn:
        rows = conn.execute(
            """SELECT m.id, m.name, m.category,
                      1 - (e.embedding <=> q.embedding) AS similarity
               FROM menu_item_embeddings e
               JOIN menu_items m ON m.id = e.menu_item_id,
                    (SELECT embedding FROM menu_item_embeddings WHERE menu_item_id = %s) q
               WHERE e.menu_item_id <> %s AND m.available
               ORDER BY e.embedding <=> q.embedding
               LIMIT %s""",
            (item_id, item_id, limit),
        ).fetchall()
    elapsed = ms(t)
    if not rows:
        raise HTTPException(404, "No embedding found for that item")
    return {
        "item_id": item_id,
        "timing_ms": elapsed,
        "recommendations": [
            {"id": i, "name": n, "category": c, "similarity": round(float(s), 3)}
            for i, n, c, s in rows
        ],
    }
