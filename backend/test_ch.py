import time
import clickhouse_connect

print("Connecting to ClickHouse...")
t0 = time.time()
client = clickhouse_connect.get_client(
    host="118.95.209.221",
    port=8123,
    username="limese_interns",
    password="ItsInterns!23",
    database="limese",
    connect_timeout=10,
)
print(f"Connected in {time.time() - t0:.2f} seconds")

print("Running count query on product_catlog...")
t0 = time.time()
result = client.query("SELECT count() FROM product_catlog")
print(f"Query returned {result.result_rows[0][0]} rows in {time.time() - t0:.2f} seconds")
