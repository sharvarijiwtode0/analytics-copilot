import time
from backend.config import settings
import clickhouse_connect

print("Connecting to ClickHouse...")
t0 = time.time()
client = clickhouse_connect.get_client(
    host=settings.clickhouse_host,
    port=settings.clickhouse_port,
    username=settings.clickhouse_user,
    password=settings.clickhouse_password,
    database=settings.clickhouse_database,
    connect_timeout=10,
)
print(f"Connected in {time.time() - t0:.2f} seconds")

print("Running count query on product_catlog...")
t0 = time.time()
result = client.query("SELECT count() FROM product_catlog")
print(f"Query returned {result.result_rows[0][0]} rows in {time.time() - t0:.2f} seconds")
