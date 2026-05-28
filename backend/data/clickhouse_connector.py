"""
ClickHouse connector for the Data Visualization Copilot.
Uses clickhouse-connect (official Python client).
Handles Limese database with all sales, inventory, product data.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import Any

import clickhouse_connect
import structlog

log = structlog.get_logger(__name__)


_client_cache: dict[tuple[str, int, str, str, str], Any] = {}


class ClickHouseConnector:
    def __init__(self, host: str, port: int, username: str, password: str, database: str) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.database = database

    def _get_client(self) -> Any:
        key = (self.host, self.port, self.username, self.password, self.database)
        global _client_cache
        if key not in _client_cache or _client_cache[key] is None:
            log.info("clickhouse.create_new_client_instance", host=self.host, port=self.port, database=self.database)
            _client_cache[key] = clickhouse_connect.get_client(
                host=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                database=self.database,
                connect_timeout=10,
                query_limit=100000,
            )
        return _client_cache[key]

    async def execute(self, sql: str, timeout: int = 30) -> dict:
        """Execute a SQL query and return {columns, rows}."""
        loop = asyncio.get_event_loop()

        def _run() -> dict:
            client = self._get_client()
            result = client.query(sql, settings={"max_execution_time": timeout})
            columns = list(result.column_names)
            rows = []
            for row in result.result_rows:
                row_dict = {}
                for col, val in zip(columns, row):
                    # Convert non-JSON-serializable types
                    if hasattr(val, 'isoformat'):
                        row_dict[col] = val.isoformat()
                    elif val is None:
                        row_dict[col] = None
                    else:
                        # Preserve SKU/ID columns as strings, not numbers
                        col_lower = col.lower()
                        is_id_column = any(id_col in col_lower for id_col in [
                            "sku", "id", "_id", "code", "pin", "zip"
                        ])

                        if is_id_column:
                            # Always convert IDs/SKUs to string to preserve leading zeros and prevent numeric conversion
                            row_dict[col] = str(val) if val is not None else None
                        elif isinstance(val, (int, float)):
                            row_dict[col] = float(val) if isinstance(val, float) else int(val)
                        else:
                            row_dict[col] = str(val)
                rows.append(row_dict)
            return {"columns": columns, "rows": rows, "row_count": len(rows)}

        try:
            return await loop.run_in_executor(None, _run)
        except Exception as exc:
            log.error("clickhouse.query_failed", error=str(exc), sql=sql[:100])
            raise

    async def get_schema(self) -> dict:
        """Get full schema with table descriptions, column info, and row counts."""
        loop = asyncio.get_event_loop()

        def _run() -> dict:
            import json
            from pathlib import Path
            client = self._get_client()

            # Get all tables
            tables_result = client.query(f"SHOW TABLES FROM {self.database}")
            all_tables = [row[0] for row in tables_result.result_rows if not row[0].startswith(".")]

            # Priority tables — the most useful for analytics, we scan these first
            PRIORITY_TABLES = [
                "combined_sales_final",
                "product_master",
                "product_catlog",
                "inventory_sales_overview_new",
                "platform_sku_mapping",
                "shopify_orders",
                "unicomm_sales_final",
                "zoho_sales_final",
                "zoho_purchase_orders",
                "inventory_ledger",
                "product_hierarchy",
                "lead_time",
            ]

            # Load existing db_intelligence if available to preserve descriptions/annotations
            db_intel_path = Path(__file__).parent.parent / "data" / "db_intelligence.json"
            existing_tables = {}
            if db_intel_path.exists():
                try:
                    with open(db_intel_path, "r") as f:
                        existing_tables = json.load(f).get("tables", {})
                except Exception as e:
                    log.warning("schema.load_db_intel_failed", error=str(e))

            # FAST PATH: If db_intelligence cache exists, use it directly!
            if existing_tables:
                tables = []
                for table_name in PRIORITY_TABLES:
                    if table_name in existing_tables and table_name in all_tables:
                        tdata = existing_tables[table_name]
                        cols = []
                        for col in tdata.get("columns", []):
                            cols.append({
                                "name": col.get("name"),
                                "type": col.get("type"),
                                "default": col.get("default", ""),
                                "null_count": col.get("null_count", 0),
                                "annotation": col.get("annotation", ""),
                            })
                        tables.append({
                            "name": table_name,
                            "columns": cols,
                            "row_count": tdata.get("row_count", 0),
                            "sample_data": tdata.get("sample_data", []),
                            "description": tdata.get("description") or TABLE_DESCRIPTIONS.get(table_name, ""),
                        })
                log.info("schema.loaded_from_db_intelligence_cache", count=len(tables))
                return {
                    "tables": tables,
                    "database": self.database,
                    "total_tables": len(all_tables),
                    "scanned_tables_count": len(tables),
                }

            # FALLBACK PATH: Fast live scan if cache is missing (skips the heavy null counting)
            log.warning("schema.db_intelligence_cache_missing_running_fast_live_fallback")
            tables_to_scan = [t for t in PRIORITY_TABLES if t in all_tables]
            tables = []
            for table_name in tables_to_scan:
                try:
                    desc = client.query(f"DESCRIBE TABLE {self.database}.{table_name}")
                    columns = [
                        {
                            "name": row[0],
                            "type": row[1],
                            "default": row[2],
                            "null_count": 0,
                            "annotation": "",
                        }
                        for row in desc.result_rows
                    ]
                    count_result = client.query(f"SELECT count() FROM {self.database}.{table_name}")
                    row_count = count_result.result_rows[0][0]

                    # Retrieve 2 sample rows quickly
                    try:
                        sample_result = client.query(f"SELECT * FROM {self.database}.{table_name} LIMIT 2")
                        sample_cols = list(sample_result.column_names)
                        sample_rows = [
                            {col: str(val) for col, val in zip(sample_cols, row)}
                            for row in sample_result.result_rows
                        ]
                    except Exception:
                        sample_rows = []

                    tables.append({
                        "name": table_name,
                        "columns": columns,
                        "row_count": row_count,
                        "sample_data": sample_rows,
                        "description": TABLE_DESCRIPTIONS.get(table_name, ""),
                    })
                    log.debug("schema.table_loaded_fallback", table=table_name, rows=row_count)
                except Exception as exc:
                    log.warning("schema.table_failed_fallback", table=table_name, error=str(exc))

            return {
                "tables": tables,
                "database": self.database,
                "total_tables": len(all_tables),
                "scanned_tables_count": len(tables),
            }

        return await loop.run_in_executor(None, _run)


# Human-readable table descriptions for the LLM context
# Updated with verified column names from live Limese database inspection
TABLE_DESCRIPTIONS = {
    "combined_sales_final": (
        "PRIMARY SALES TABLE — all orders across 13 platforms. 340K+ rows, Jan 2025–present. "
        "Total revenue ~₹570 crore. Total units ~5.7M (quantity_ordered). "
        "EXACT platform names (case-sensitive for WHERE filters): "
        "  'direct_sales' (131K orders), 'Nykaa Beauty' (121K), 'POS' (37K), "
        "  'Myntra_PPMP' (19K), 'Nykaa Man' (11K), 'LIMESE_ONLINE' (5.8K), "
        "  'Flipkart_Earn_More' (5.3K), 'TIRA Online' (3.5K), 'Shopify' (1.6K), "
        "  'AJIO Online' (1.4K), 'TIRA Offline' (1.4K), 'AZORTE Online' (114). "
        "CRITICAL COLUMN NAMES (use exactly): "
        "  date_created (DateTime — primary date), sales_platform (exact names above), "
        "  internal_sku (join key to product_master), "
        "  row_subtotal (revenue per line — USE FOR REVENUE), "
        "  quantity_ordered (units ordered — USE FOR UNITS, NOT shipped_qty which is 0), "
        "  final_status, product_mrp, product_sp. "
        "ALWAYS add: final_status NOT IN ('cancelled','Cancelled','CANCELLED','returned','Returned'). "
        "DATE FUNCTIONS: toYear(date_created), toMonth(date_created), "
        "  formatDateTime(date_created, '%Y-%m') for year-month. "
        "JOIN: LEFT JOIN product_master pm ON csf.internal_sku = pm.internal_sku"
    ),
    "product_master": (
        "Product catalog — 1,383 unique internal SKUs. "
        "COLUMNS: internal_sku (PK, join key), item_name, brand, "
        "category_l1 (values: 'Skincare' 963 SKUs, 'Makeup' 409 SKUs, 'Haircare' 11 SKUs), "
        "category_l2, category_l3, size, color, mrp, cogs, gst_tax_percent. "
        "Use for: product-level analysis, category breakdown, margin calculation (mrp - cogs)."
    ),
    "product_catlog": (
        "Platform SKU catalog — 5,586 rows mapping internal to external SKUs. "
        "COLUMNS: internal_sku, external_sku, platform, product_name, brand, mrp, cogs, category. "
        "Use to find platform-specific listing info."
    ),
    "inventory_sales_overview_new": (
        "Daily inventory snapshot — 1.1M rows, Sep 2025–present. "
        "COLUMNS: sku (internal SKU = join to product_master.internal_sku), "
        "date (DateTime), facility, "
        "inventory (units on hand — USE FOR STOCK LEVELS), "
        "order_quantity (units sold that day), gross_sales_rs (daily revenue), "
        "sp (selling price), mrp, cogs, burn_period (fixed at 90 days). "
        "Current stock: Skincare=1,693 units/632 SKUs, Makeup=476 units/334 SKUs, Haircare=39 units. "
        "For CURRENT inventory: WHERE date = (SELECT max(date) FROM inventory_sales_overview_new WHERE inventory > 0). "
        "For LOW STOCK: WHERE inventory < 100 AND inventory > 0. "
        "For OOS: WHERE inventory = 0."
    ),
    "platform_sku_mapping": (
        "Maps internal SKUs to platform-specific codes. "
        "Key columns: internal_sku, platform, external_code, product_name. "
        "Use to understand which internal SKU corresponds to platform listings."
    ),
    "shopify_orders": (
        "Shopify D2C website orders. Key columns: name (order ID), created_at, total, "
        "lineitem_sku, lineitem_price, lineitem_quantity, financial_status, billing_city, "
        "billing_province (state), discount_code."
    ),
    "unicomm_sales_final": (
        "Unicommerce OMS sales. B2B and marketplace orders. "
        "Key columns: order_id, channel_created_at, order_channel_name (marketplace), "
        "sku, item_price, qty, shipment_tracking_number, order_status, final_status."
    ),
    "zoho_sales_final": (
        "Zoho Books sales orders. B2B and trade sales. "
        "Key columns: so_order_date, so_sku, so_item_name, so_customer_name, "
        "so_sales_channel, so_quantity_invoiced, so_rate, so_item_total, so_status."
    ),
    "zoho_purchase_orders": (
        "Purchase orders from vendors. "
        "Key columns: po_date, po_vendor_name, po_line_item_description, po_sku, "
        "po_quantity_received, po_rate, po_item_total, po_status."
    ),
    "inventory_ledger": (
        "Stock movement ledger — inflows and outflows. "
        "Use for: stock reconciliation, vendor receipts, dispatch tracking."
    ),
    "lead_time": (
        "Vendor lead times by SKU/vendor. "
        "Key columns: vendor, sku, avg_lead_time_days. "
        "Use for: reorder planning, supply chain analysis."
    ),
    "product_hierarchy": (
        "Product parent-child relationships (combo/kit/variant). "
        "Key columns: relationship_type, parent_sku, child_sku, quantity."
    ),
}


# Singleton instance
_limese_connector: ClickHouseConnector | None = None


def get_limese_connector() -> ClickHouseConnector:
    global _limese_connector
    if _limese_connector is None:
        _limese_connector = ClickHouseConnector(
            host="118.95.209.221",
            port=8123,
            username="limese_interns",
            password="ItsInterns!23",
            database="limese",
        )
    return _limese_connector
