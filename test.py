"""PoC test script. Run inside the container:
    docker compose exec api python test.py
Prints timings you can copy into Part 5 (PoC Results)."""
import json
import time
import urllib.request

BASE = "http://localhost:8000"


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def row(name, values):
    v = sorted(values)
    p95 = v[int(0.95 * (len(v) - 1))]
    print(f"{name:<34}{len(v):>5}{sum(v)/len(v):>9.2f}{v[0]:>9.2f}{p95:>9.2f}{v[-1]:>9.2f}")


print("== Menu cache (R4) ==")
first, second = call("GET", "/menu"), call("GET", "/menu")
print(f"1st call: {first['source']} {first['timing_ms']} ms | 2nd call: {second['source']} {second['timing_ms']} ms\n")

print("== Scenario 3: order in PostgreSQL + live status in Redis ==")
N = 30
pg_insert, redis_set = [], []
order_id = None
for i in range(N):
    res = call("POST", "/orders", {"customer_id": 1, "items": [{"menu_item_id": 1, "quantity": 2}, {"menu_item_id": 13, "quantity": 1}]})
    order_id = res["order_id"]
    pg_insert.append(res["timing_ms"]["postgres_insert"])
    redis_set.append(res["timing_ms"]["redis_set"])

redis_read = [call("GET", f"/orders/{order_id}/status")["timing_ms"] for _ in range(100)]
pg_read = [call("GET", f"/orders/{order_id}/status?source=postgres")["timing_ms"] for _ in range(100)]

print("\nStatus walkthrough for order", order_id)
for s in ["preparing", "picked_up", "on_the_way", "delivered"]:
    res = call("PATCH", f"/orders/{order_id}/status", {"status": s})
    print(f"  -> {s:<11} {res['timing_ms']}")
print("  final status (Redis):", call("GET", f"/orders/{order_id}/status")["status"])
print("  final status (PostgreSQL):", call("GET", f"/orders/{order_id}/status?source=postgres")["status"])

print("\n== Recommendations (R5): similar to item 1 (Chicken Burger Deluxe) ==")
reco = [call("GET", "/recommendations?item_id=1")["timing_ms"] for _ in range(30)]
for x in call("GET", "/recommendations?item_id=1")["recommendations"]:
    print(f"  {x['name']:<26} {x['category']:<10} similarity {x['similarity']}")

print("\nTimings in milliseconds (server-side)")
print(f"{'Operation':<34}{'n':>5}{'avg':>9}{'min':>9}{'p95':>9}{'max':>9}")
row("PostgreSQL order insert (commit)", pg_insert)
row("Redis status write", redis_set)
row("Redis status read", redis_read)
row("PostgreSQL status read", pg_read)
row("pgvector similarity search", reco)
