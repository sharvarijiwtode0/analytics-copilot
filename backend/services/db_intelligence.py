"""
Database Intelligence Layer — Limese ClickHouse
================================================
Scans the connected database (READ-ONLY) and builds a comprehensive
context document that is injected into every LLM SQL-generation prompt.

What it extracts per table:
  - Row count & date range
  - Every column: type, unique count, exact categorical values (≤ 200 unique)
  - Key business facts (total revenue, top platforms, date coverage)
  - Column-level annotations (which col = revenue, units, date, etc.)
  - Common query patterns validated against real data

Auto-refreshes every REFRESH_HOURS on a background thread.
Context stored at: /tmp/dvc_metadata/db_intelligence.json
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import clickhouse_connect
import structlog
from backend.data.clickhouse_connector import TABLE_DESCRIPTIONS

log = structlog.get_logger(__name__)

CONTEXT_FILE = Path(__file__).parent.parent / "data" / "db_intelligence.json"
CONTEXT_FILE.parent.mkdir(parents=True, exist_ok=True)

REFRESH_HOURS = 24         # auto-refresh every 24 hours
MAX_CATEGORICAL = 200      # if unique values ≤ this, store all exact values

# Tables to scan deeply — priority order
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

# Hard-coded business annotations layered on top of auto-discovered schema
# These are facts the LLM MUST know to write correct SQL
COLUMN_ANNOTATIONS: dict[str, dict[str, str]] = {
    "combined_sales_final": {
        "sales_platform":   "DIMENSION — use this to GROUP BY platform. Contains exact platform names shown below.",
        "platform":         "DIMENSION — sales channel. Exact values: 'Shopee', 'Shopify', 'Tokopedia', 'offline', 'Lazada'. GROUP BY platform for channel breakdowns.",
        "client_name":      "CONSTANT 'Limese' for all rows — NEVER group by this for platform analysis.",
        "order_id":         "Unique order identifier. Use COUNT(DISTINCT order_id) for order volume. Do NOT use count() alone (counts line items, not orders).",
        "row_subtotal":     "REVENUE per line item — USE THIS for revenue/sales. Do NOT use order_price (full order total).",
        "quantity_ordered": "UNITS per line — USE THIS for unit counts. Do NOT use shipped_qty (always 0).",
        "date_created":     "Primary date column. Filter: date_created >= '2025-01-01'. Group: formatDateTime(date_created, '%Y-%m') AS month, or '%Y-%m-%d' AS date for daily trends.",
        "final_status":     "Order outcome. ALWAYS exclude: NOT IN ('cancelled','Cancelled','CANCELLED','returned','Returned').",
        "internal_sku":     "Join key → product_master.internal_sku for product names, category, MRP, COGS.",
        "external_sku":     "Platform-specific SKU code. Use internal_sku for cross-table JOINs instead.",
    },
    "product_master": {
        "internal_sku":   "Primary key. JOIN combined_sales_final csf ON csf.internal_sku = pm.internal_sku.",
        "item_name":      "Human-readable product display name. USE THIS for product-level GROUP BY and labels.",
        "product_name":   "Human-readable product display name (alias of item_name). USE THIS for product-level GROUP BY.",
        "category_l1":    "Top-level product category. Values: 'Skincare', 'Makeup', 'Haircare'. USE THIS for category breakdowns.",
        "mrp":            "Maximum Retail Price per unit — use for revenue potential analysis.",
        "cogs":           "Cost of Goods Sold per unit — use for margin: (mrp - cogs) / mrp * 100 AS margin_pct.",
    },
    "inventory_sales_overview_new": {
        "sku":             "Internal SKU — JOIN to product_master.internal_sku for product names.",
        "date":            "Snapshot date. For CURRENT live stock use: WHERE date >= today() - 2. Do NOT omit this filter.",
        "inventory":       "Units currently on hand. USE THIS for all stock level, availability, and inventory queries.",
        "order_quantity":  "Units sold on that specific day.",
        "gross_sales_rs":  "Daily revenue in ₹ for that day.",
        "burn_period":     "Fixed 90-day config value — do NOT use for calculations or trend analysis.",
    },
    "shopify_orders": {
        "subtotal":           "REVENUE — USE THIS for Shopify revenue/sales totals. Not 'total' (includes taxes).",
        "total":              "Order total including taxes — use subtotal instead for pure revenue.",
        "lineitem_price":     "Price per line item.",
        "lineitem_quantity":  "Units per line item — USE THIS for unit counts on Shopify.",
        "created_at":         "Primary date column for Shopify. Filter: created_at >= '2025-01-01'. Group: formatDateTime(created_at, '%Y-%m').",
        "financial_status":   "Payment state. EXCLUDE refunded: WHERE financial_status NOT IN ('refunded','partially_refunded').",
        "fulfillment_status": "Shipping state: 'fulfilled' = shipped, 'unfulfilled' = pending.",
        "lineitem_name":      "Product name per line item — USE for product-level Shopify queries.",
    },
    "zoho_sales_final": {
        "so_item_rate":      "UNIT PRICE — price per unit for this line item.",
        "so_quantity":       "UNITS ordered for this line item.",
        "so_item_total":     "REVENUE per line — USE THIS for Zoho revenue/sales totals.",
        "so_subtotal":       "Order subtotal before tax — use so_item_total for line-level revenue.",
        "so_total":          "Order total including tax.",
        "so_order_date":     "Primary date column for Zoho. Filter: so_order_date >= '2025-01-01'. Group: formatDateTime(so_order_date, '%Y-%m').",
        "so_status":         "Order status. EXCLUDE: WHERE so_status NOT IN ('Cancelled','Returned').",
        "so_customer_name":  "B2B customer/distributor name — USE for distributor-level analysis.",
    },
}

TABLE_ALIASES = {
    "combined_sales_final": ["overall sales", "total revenue", "combined revenue", "sales platform", "sales channels"],
    "product_master": ["products", "categories", "sku mrp", "cogs", "margins"],
    "product_catlog": ["catalog", "listing catalogue"],
    "inventory_sales_overview_new": ["inventory stock", "warehouse quantity", "stock levels", "sell-through"],
    "platform_sku_mapping": ["sku mapping", "external sku map"],
    "shopify_orders": ["shopify sales", "online sales", "storefront orders", "website transactions"],
    "unicomm_sales_final": ["unicommerce sales", "unicomm channel"],
    "zoho_sales_final": ["zoho sales", "zoho invoices", "zoho B2B"],
    "zoho_purchase_orders": ["zoho procurement", "zoho purchase orders", "supplier po"],
    "inventory_ledger": ["stock movements", "ledger history", "inventory log"],
    "product_hierarchy": ["category tree", "brand hierarchy"],
    "lead_time": ["replenishment time", "delivery days", "supplier latency"],
}

# ─── Core scanner ─────────────────────────────────────────────────────────────

def _get_client() -> Any:
    return clickhouse_connect.get_client(
        host="118.95.209.221", port=8123,
        username="limese_interns", password="ItsInterns!23",
        database="limese", connect_timeout=10,
    )


def _scan_table(client: Any, table: str, existing_table: dict | None = None, deep_scan: bool = True) -> dict:
    """Scan a table (deep-scan for priority tables, shallow-scan for others)."""
    log.info("db_intelligence.scanning", table=table, deep=deep_scan)

    # Row count
    try:
        cnt = client.query(f"SELECT count() FROM {table}").result_rows[0][0]
    except Exception:
        cnt = 0

    # Schema
    try:
        schema = client.query(f"DESCRIBE TABLE {table}").result_rows
        columns_raw = [{"name": r[0], "type": r[1]} for r in schema]
    except Exception:
        return {"table": table, "error": "could not describe table", "row_count": 0}

    annotations = COLUMN_ANNOTATIONS.get(table, {})
    columns_info: list[dict] = []

    for col in columns_raw:
        col_name = col["name"]
        col_type = col["type"]
        
        # Check if we have an annotation in existing_table first, then fall back to COLUMN_ANNOTATIONS
        existing_col_annotation = ""
        if existing_table:
            for old_col in existing_table.get("columns", []):
                if old_col.get("name") == col_name and old_col.get("annotation"):
                    existing_col_annotation = old_col["annotation"]
                    break
        
        info: dict = {
            "name": col_name,
            "type": col_type,
            "annotation": existing_col_annotation or annotations.get(col_name, ""),
        }

        # Try to get unique count + sample values
        unique = 0
        if deep_scan:
            try:
                r = client.query(
                    f"SELECT uniq(`{col_name}`) as u, count(`{col_name}`) as nn "
                    f"FROM {table}"
                )
                unique = int(r.result_rows[0][0])
                non_null = int(r.result_rows[0][1])
                info["unique_count"] = unique
                info["non_null_count"] = non_null
                info["null_count"] = max(0, cnt - non_null)

                # Fetch all values for low-cardinality columns
                if 1 < unique <= MAX_CATEGORICAL:
                    try:
                        sv = client.query(
                            f"SELECT DISTINCT `{col_name}` FROM {table} "
                            f"WHERE `{col_name}` IS NOT NULL LIMIT {MAX_CATEGORICAL}"
                        )
                        vals = [str(r[0]) for r in sv.result_rows if r[0] is not None]
                        info["exact_values"] = sorted(vals)
                        info["is_categorical"] = True
                    except Exception:
                        pass
                elif unique == 1:
                    # Constant column
                    try:
                        cv = client.query(f"SELECT `{col_name}` FROM {table} LIMIT 1")
                        info["constant_value"] = str(cv.result_rows[0][0]) if cv.result_rows else "?"
                        info["is_constant"] = True
                    except Exception:
                        pass
            except Exception:
                pass

        # Date range for date columns
        type_lower = col_type.lower()
        if deep_scan and ("date" in type_lower or "time" in type_lower):
            try:
                dr = client.query(
                    f"SELECT toString(min(`{col_name}`)), toString(max(`{col_name}`)) FROM {table}"
                )
                info["date_range"] = {"min": str(dr.result_rows[0][0]), "max": str(dr.result_rows[0][1])}
            except Exception:
                pass

        # Numerical range for numeric columns
        if deep_scan and any(t in type_lower for t in ["int", "float", "decimal"]) and unique > MAX_CATEGORICAL:
            try:
                nr = client.query(
                    f"SELECT round(min(`{col_name}`),2), round(max(`{col_name}`),2), round(avg(`{col_name}`),2) FROM {table}"
                )
                info["numerical_range"] = {
                    "min": float(nr.result_rows[0][0] or 0),
                    "max": float(nr.result_rows[0][1] or 0),
                    "avg": float(nr.result_rows[0][2] or 0),
                }
            except Exception:
                pass

        columns_info.append(info)

    result = {
        "table": table,
        "row_count": cnt,
        "total_columns": len(columns_info),
        "columns": columns_info,
    }

    # Sample 2 rows
    result["sample_data"] = []
    if deep_scan:
        try:
            sample_result = client.query(f"SELECT * FROM {table} LIMIT 2")
            sample_cols = list(sample_result.column_names)
            sample_rows = [
                {col: str(val) for col, val in zip(sample_cols, row)}
                for row in sample_result.result_rows
            ]
            result["sample_data"] = sample_rows
        except Exception:
            pass

    # Table-level business facts
    if table == "combined_sales_final":
        try:
            facts = client.query("""
                SELECT
                    round(sum(ifNull(row_subtotal,0))/1e7, 2) as revenue_crore,
                    count() as total_orders,
                    round(sum(ifNull(quantity_ordered,0)),0) as total_units,
                    min(date_created) as earliest,
                    max(date_created) as latest
                FROM combined_sales_final
                WHERE final_status NOT IN ('cancelled','Cancelled','CANCELLED','returned','Returned')
            """)
            row = facts.result_rows[0]
            result["business_facts"] = {
                "total_revenue_crore": float(row[0] or 0),
                "total_orders": int(row[1] or 0),
                "total_units": int(row[2] or 0),
                "date_range": f"{row[3]} to {row[4]}",
            }
        except Exception:
            pass

    if table == "inventory_sales_overview_new":
        try:
            inv = client.query("""
                SELECT count(DISTINCT sku) as skus, round(sum(inventory),0) as total_units
                FROM inventory_sales_overview_new
                WHERE date >= today() - 2
            """)
            row = inv.result_rows[0]
            result["business_facts"] = {
                "tracked_skus": int(row[0] or 0),
                "total_inventory_units": int(row[1] or 0),
            }
        except Exception:
            pass

    # Load from priority descriptions/aliases, falling back to existing context or defaults
    desc = TABLE_DESCRIPTIONS.get(table)
    if not desc:
        desc = existing_table.get("description", "") if existing_table else ""
    if not desc:
        t_low = table.lower()
        if "sales" in t_low:
            desc = f"Operational sales dataset for the {table.split('_')[0].capitalize()} channel."
        elif "inventory" in t_low or "stock" in t_low:
            desc = f"Inventory and stock balance log for {table.replace('_', ' ')}."
        elif "product" in t_low or "sku" in t_low:
            desc = f"Product master metadata for {table.replace('_', ' ')}."
        else:
            desc = f"Operational dataset related to {table.replace('_', ' ')}."

    aliases = TABLE_ALIASES.get(table)
    if not aliases:
        aliases = existing_table.get("aliases", []) if existing_table else []

    result["description"] = desc
    result["aliases"] = aliases

    return result


def build_db_context() -> dict:
    """
    Full database scan (READ-ONLY).
    Returns a comprehensive context dict with all tables, columns, values, and facts.
    Takes 30-90 seconds on first run; cached to disk.
    """
    client = _get_client()
    t0 = time.time()

    # Load existing context if available to preserve descriptions, aliases, etc.
    existing_context = {}
    if CONTEXT_FILE.exists():
        try:
            with open(CONTEXT_FILE, "r") as f:
                existing_context = json.load(f)
        except Exception as e:
            log.warning("db_intelligence.load_existing_failed", error=str(e))

    existing_tables = existing_context.get("tables", {})

    # Scan all 173 tables with tiered scan
    try:
        tables_res = client.query("SHOW TABLES").result_rows
        all_tables = [r[0] for r in tables_res if not r[0].startswith(".")]
    except Exception as e:
        log.warning("db_intelligence.show_tables_failed", error=str(e))
        all_tables = PRIORITY_TABLES

    log.info(
        "db_intelligence.starting_scan",
        total_tables=len(all_tables),
        priority_tables=len(PRIORITY_TABLES)
    )

    tables_context = {}
    for table in all_tables:
        try:
            existing_table = existing_tables.get(table)
            is_priority = table in PRIORITY_TABLES
            tables_context[table] = _scan_table(client, table, existing_table, deep_scan=is_priority)
        except Exception as exc:
            log.error("db_intelligence.table_failed", table=table, error=str(exc))
            tables_context[table] = {"table": table, "error": str(exc)}

    elapsed = round(time.time() - t0, 1)
    context = {
        "database": "limese",
        "host": "118.95.209.221:8123",
        "scanned_at": datetime.utcnow().isoformat(),
        "scan_duration_seconds": elapsed,
        "tables": tables_context,
        "global_notes": _build_global_notes(tables_context),
    }

    # Save to disk
    try:
        with open(CONTEXT_FILE, "w") as f:
            json.dump(context, f, indent=2, default=str)
        log.info("db_intelligence.saved", path=str(CONTEXT_FILE), seconds=elapsed)
    except Exception as exc:
        log.error("db_intelligence.save_failed", error=str(exc))

    return context


def _build_global_notes(tables: dict) -> list[str]:
    """Human-readable rules derived from the scan, injected as LLM instructions.

    All notes are derived dynamically from the actual scan results —
    no hardcoded database or brand names.
    """
    notes = [
        "READ-ONLY: Never generate INSERT/UPDATE/DELETE/DROP/CREATE/ALTER SQL.",
    ]

    # Derive database-level context from the scanned tables
    total_rows = sum(t.get("row_count", 0) for t in tables.values() if isinstance(t, dict))
    table_names = [t for t in tables.keys() if isinstance(tables[t], dict) and not tables[t].get("error")]
    if table_names:
        notes.append(f"DATABASE TABLES ({len(table_names)}): {', '.join(table_names[:15])}")
    if total_rows:
        notes.append(f"TOTAL ROWS: {total_rows:,}")

    # Extract useful column-level insights from annotated tables
    for tname, tdata in tables.items():
        if not isinstance(tdata, dict) or tdata.get("error"):
            continue

        for col in tdata.get("columns", []):
            annotation = col.get("annotation", "")
            col_name = col.get("name", "")

            # Surface exact categorical values for the LLM (e.g. platform names, statuses)
            if col.get("exact_values") and col.get("is_categorical"):
                vals = col["exact_values"]
                if len(vals) <= 25:
                    notes.append(
                        f"{tname}.{col_name} — exact values (case-sensitive): {vals}"
                    )

            # Surface constant columns so the LLM doesn't group by them
            if col.get("is_constant") and col.get("constant_value"):
                notes.append(
                    f"{tname}.{col_name} is ALWAYS '{col['constant_value']}' — NEVER group by this."
                )

            # Surface key annotations
            if annotation and any(kw in annotation.upper() for kw in ["REVENUE", "USE THIS", "PRIMARY", "DIMENSION", "MANDATORY", "NEVER"]):
                notes.append(f"{tname}.{col_name}: {annotation}")

        # Surface business facts if available
        if tdata.get("business_facts"):
            facts = tdata["business_facts"]
            facts_str = ", ".join(f"{k}: {v}" for k, v in facts.items())
            notes.append(f"{tname} facts: {facts_str}")

    # ClickHouse-specific tips (applicable to all ClickHouse databases)
    notes += [
        "CLICKHOUSE FUNCTIONS: ifNull(col, 0), uniq(), groupArray(), toDate(), formatDateTime().",
        "LIMIT: Always add LIMIT (max 10000 for detail, 50 for aggregations).",
    ]
    return notes


# ─── Columns that MUST always appear in the prompt regardless of question ─────
# These are the backbone of 95%+ of all valid ClickHouse queries.
MANDATORY_COLUMNS: dict[str, set[str]] = {
    "combined_sales_final": {"row_subtotal", "quantity_ordered", "date_created", "final_status", "internal_sku", "order_id", "platform"},
    "product_master": {"internal_sku", "item_name", "product_name", "category_l1", "mrp", "cogs"},
    "inventory_sales_overview_new": {"sku", "date", "inventory", "order_quantity", "gross_sales_rs"},
    "shopify_orders": {"subtotal", "lineitem_quantity", "created_at", "financial_status", "lineitem_name"},
    "zoho_sales_final": {"so_item_total", "so_quantity", "so_order_date", "so_status", "so_customer_name"},
}


def _score_column_for_question(col: dict, question: str, table: str, mandatory_cols: set[str]) -> int:
    """
    Score a column by how relevant it is to the user's question.
    Higher score = show earlier in the prompt.
    Mandatory columns get a guaranteed-high base score so they always rise to the top.
    Score is ADDITIVE only — nothing is ever hard-excluded.
    """
    col_name = col.get("name", "").lower()
    annotation = (col.get("annotation", "") or "").lower()
    q = question.lower()

    score = 0

    # Mandatory columns always score highest — they are never cut
    if col_name in mandatory_cols:
        score += 100

    # Annotated columns are always valuable
    if annotation:
        score += 20
        if any(kw in annotation for kw in ["use this", "primary", "mandatory", "revenue", "units", "join key"]):
            score += 15

    # Categorical columns with exact values are immediately useful
    if col.get("exact_values") or col.get("is_categorical"):
        score += 10

    # Question-keyword matching
    kw_groups = [
        (["revenue", "sales", "subtotal", "income", "earning", "performance", "value", "amount", "spend"],
         ["row_subtotal", "subtotal", "so_item_total", "gross_sales_rs"]),
        (["unit", "qty", "quantity", "volume", "count"],
         ["quantity_ordered", "lineitem_quantity", "so_quantity", "order_quantity", "inventory"]),
        (["month", "day", "date", "trend", "year", "quarter", "weekly", "daily", "monthly", "yearly"],
         ["date_created", "created_at", "so_order_date", "date"]),
        (["platform", "channel", "shopee", "shopify", "tokopedia", "offline", "lazada"],
         ["platform", "sales_platform"]),
        (["product", "sku", "item", "listing"],
         ["item_name", "product_name", "lineitem_name", "internal_sku", "external_sku"]),
        (["category", "skincare", "makeup", "haircare", "segment"],
         ["category_l1"]),
        (["margin", "profit", "cogs", "cost", "mrp", "price"],
         ["mrp", "cogs", "lineitem_price", "so_item_rate"]),
        (["stock", "inventory", "warehouse", "available", "on hand"],
         ["inventory", "order_quantity", "gross_sales_rs"]),
        (["order", "transaction", "purchase"],
         ["order_id", "final_status", "financial_status", "so_status"]),
        (["customer", "buyer", "client", "distributor", "b2b"],
         ["so_customer_name"]),
        (["aov", "average order"],
         ["order_id", "row_subtotal"]),
    ]

    for question_keywords, relevant_cols in kw_groups:
        if any(kw in q for kw in question_keywords):
            if col_name in relevant_cols:
                score += 25  # strong signal match
            elif any(rc in col_name for rc in relevant_cols):
                score += 10  # partial name match

    # Constant columns with no real utility score low (but not excluded!)
    if col.get("is_constant"):
        score -= 10  # deprioritize but still show if budget allows

    return score


# ─── LLM prompt builder ───────────────────────────────────────────────────────

def build_sql_context_prompt(
    context: dict,
    question: str,
    relevant_tables: list[str] | None = None,
    max_cols_per_table: int = 12,   # raised from 8 — relevance scoring means all 12 are useful
    max_cat_values: int = 10,        # raised from 5 — more exact values = fewer hallucinated names
):
    """
    Convert the DB intelligence context into a COMPACT LLM-ready string.
    Stays well within model token limits by:
    - Only including relevant tables
    - Skipping columns with no useful info (no annotation, no categorical values, no range)
    - Capping categorical values at max_cat_values
    - Prioritizing annotated and categorical columns
    """
    lines: list[str] = []

    # Global rules — deduplicated, highest-signal notes only (cap at 15).
    # Priority rules are surfaced first; low-signal/redundant notes dropped.
    notes_raw = context.get("global_notes", [])
    PRIORITY_RULE_KEYWORDS = ["revenue", "use this", "exclude", "mandatory", "do not", "never", "join", "primary date", "clickhouse"]
    seen_notes: set[str] = set()
    priority_notes: list[str] = []
    other_notes: list[str] = []
    for note in notes_raw:
        norm = note.strip().lower()
        if norm in seen_notes:
            continue
        seen_notes.add(norm)
        if any(kw in norm for kw in PRIORITY_RULE_KEYWORDS):
            priority_notes.append(note)
        else:
            other_notes.append(note)
    notes = priority_notes + other_notes
    if notes:
        lines.append("=== CRITICAL RULES ===")
        for note in notes[:15]:  # tight cap — only 15 highest-signal rules
            lines.append(f"• {note}")
        lines.append("")

    # Table schemas — only relevant, only useful columns
    lines.append("=== DATABASE SCHEMA ===")
    tables = context.get("tables", {})

    # If no relevant_tables specified, include the 2 most important ones
    tables_to_show = relevant_tables or ["combined_sales_final", "product_master"]

    for tname in tables_to_show:
        tdata = tables.get(tname, {})
        if not tdata or tdata.get("error"):
            # Dynamically shallow scan the missing table!
            try:
                client = _get_client()
                cnt = client.query(f"SELECT count() FROM {tname}").result_rows[0][0]
                schema = client.query(f"DESCRIBE TABLE {tname}").result_rows
                cols = []
                for r in schema:
                    cols.append({
                        "name": r[0],
                        "type": r[1],
                        "annotation": COLUMN_ANNOTATIONS.get(tname, {}).get(r[0], ""),
                    })
                tdata = {
                    "table": tname,
                    "row_count": cnt,
                    "columns": cols,
                }
                # Cache it locally in context so we don't repeat the scan next time
                tables[tname] = tdata
            except Exception as e:
                log.warning("db_intelligence.dynamic_scan_failed", table=tname, error=str(e))
                continue

        row_count = tdata.get("row_count", 0)
        desc = tdata.get("description") or TABLE_DESCRIPTIONS.get(tname, "")
        desc_str = f" — {desc}" if desc else ""
        lines.append(f"\nTABLE: {tname} ({row_count:,} rows){desc_str}")

        # Business facts
        if tdata.get("business_facts"):
            facts = tdata["business_facts"]
            lines.append(f"  Facts: {json.dumps(facts)}")

        # Sort columns by relevance score (question-aware) so the most useful columns
        # for THIS specific question appear first and fill the budget.
        # Always overlay live COLUMN_ANNOTATIONS so new annotations apply immediately
        # without requiring a 24-hour cache refresh.
        live_annotations = COLUMN_ANNOTATIONS.get(tname, {})
        mandatory_for_table = MANDATORY_COLUMNS.get(tname, set())
        all_cols = tdata.get("columns", [])

        # Apply live annotation overlay before scoring
        for c in all_cols:
            if live_annotations.get(c["name"]):
                c = {**c, "annotation": live_annotations[c["name"]]}

        # Score each column for relevance to the question
        scored_cols = sorted(
            all_cols,
            key=lambda c: _score_column_for_question(c, question, tname, mandatory_for_table),
            reverse=True,
        )
        cols_to_show = scored_cols[:max_cols_per_table]

        for col in cols_to_show:
            col_name = col["name"]
            col_type = col["type"]
            # Live annotation takes precedence over cached one
            annotation = live_annotations.get(col_name) or col.get("annotation", "")

            col_line = f"  • `{col_name}` ({col_type})"

            if col.get("is_constant"):
                col_line += f" — CONSTANT='{col.get('constant_value')}' (never GROUP BY this)"
            elif col.get("exact_values"):
                vals = col["exact_values"][:max_cat_values]
                col_line += f" — VALUES: {vals}"
            elif col.get("date_range"):
                dr = col["date_range"]
                col_line += f" — range: {dr['min'][:10]} to {dr['max'][:10]}"
            elif col.get("numerical_range"):
                nr = col["numerical_range"]
                col_line += f" — range: {nr['min']:,.0f} to {nr['max']:,.0f} (avg: {nr['avg']:,.0f})"

            if annotation:
                col_line += f"\n      ↳ {annotation}"

            lines.append(col_line)

    return "\n".join(lines)


# ─── Context loading / caching ────────────────────────────────────────────────

_context_cache: dict | None = None
_context_loaded_at: float = 0.0
_build_lock = threading.Lock()


def _build_minimal_fast_context() -> dict:
    """
    Build a minimal table/column schema context in < 0.2 seconds.
    Used on first run as an instant fallback so startup never blocks.
    """
    log.info("db_intelligence.building_minimal_fast_context")
    t0 = time.time()
    tables_context = {}
    try:
        client = _get_client()
        tables_res = client.query("SHOW TABLES").result_rows
        all_tables = {r[0] for r in tables_res if not r[0].startswith(".")}
        tables_to_scan = [t for t in PRIORITY_TABLES if t in all_tables] or PRIORITY_TABLES

        for table in tables_to_scan:
            try:
                schema = client.query(f"DESCRIBE TABLE {table}").result_rows
                columns = [{"name": r[0], "type": r[1], "annotation": COLUMN_ANNOTATIONS.get(table, {}).get(r[0], "")} for r in schema]
                tables_context[table] = {
                    "table": table,
                    "row_count": 0,
                    "total_columns": len(columns),
                    "columns": columns,
                    "description": "",
                    "aliases": [],
                }
            except Exception:
                tables_context[table] = {"table": table, "error": "could not describe table"}
    except Exception as exc:
        log.warning("db_intelligence.fast_context_failed", error=str(exc))

    elapsed = round(time.time() - t0, 2)
    log.info("db_intelligence.fast_context_built", seconds=elapsed)
    return {
        "database": "limese",
        "host": "118.95.209.221:8123",
        "scanned_at": datetime.utcnow().isoformat(),
        "scan_duration_seconds": elapsed,
        "tables": tables_context,
        "global_notes": ["DATABASE TABLES: " + ", ".join(tables_context.keys())],
    }


def get_db_context(force_refresh: bool = False) -> dict:
    """
    Return the DB intelligence context.
    Uses stale-while-revalidate pattern:
      - Returns cached in-memory/disk context IMMEDIATELY (no blocking)
      - Kicks off database scans in a background thread to prevent keeping the user waiting
      - On clean boot with zero cache, builds a schema in < 0.2s as a fast fallback
    """
    global _context_cache, _context_loaded_at

    # 1. Return in-memory cache immediately if fresh
    if _context_cache and not force_refresh:
        age_hours = (time.time() - _context_loaded_at) / 3600
        if age_hours < REFRESH_HOURS:
            return _context_cache

    # 2. Try loading from disk (fast, non-blocking)
    disk_ctx = None
    if CONTEXT_FILE.exists():
        try:
            with open(CONTEXT_FILE) as f:
                disk_ctx = json.load(f)
        except Exception as exc:
            log.warning("db_intelligence.disk_load_failed", error=str(exc))

    # 3. Handle Stale-While-Revalidate
    if disk_ctx:
        scanned_at_str = disk_ctx.get("scanned_at", "")
        try:
            scanned_at = datetime.fromisoformat(scanned_at_str)
            age_hours = (time.time() - scanned_at.timestamp()) / 3600
        except Exception:
            age_hours = 999.0

        if age_hours < REFRESH_HOURS and not force_refresh:
            _context_cache = disk_ctx
            _context_loaded_at = time.time()
            log.info("db_intelligence.loaded_from_disk", age_hours=round(age_hours, 1))
            return _context_cache

        # Cache is stale or refresh forced — update memory cache and scan asynchronously
        log.info("db_intelligence.revalidating_stale_cache_in_background", age_hours=round(age_hours, 1))
        _context_cache = disk_ctx
        _context_loaded_at = time.time()

        # Revalidate in a background thread
        def _revalidate():
            with _build_lock:
                try:
                    global _context_cache, _context_loaded_at
                    fresh_ctx = build_db_context()
                    _context_cache = fresh_ctx
                    _context_loaded_at = time.time()
                except Exception as e:
                    log.error("db_intelligence.background_revalidation_failed", error=str(e))

        threading.Thread(target=_revalidate, daemon=True, name="db-intelligence-revalidate").start()
        return disk_ctx

    # 4. Clean boot fallback — absolutely zero cache exists
    # To keep startup under 1s, we populate a minimal fast context immediately,
    # and then trigger the heavy deep scan asynchronously.
    log.info("db_intelligence.clean_boot_no_cache_detected")
    fast_ctx = _build_minimal_fast_context()
    _context_cache = fast_ctx
    _context_loaded_at = time.time()

    # Trigger heavy deep scan in the background
    def _first_time_deep_scan():
        with _build_lock:
            try:
                global _context_cache, _context_loaded_at
                deep_ctx = build_db_context()
                _context_cache = deep_ctx
                _context_loaded_at = time.time()
            except Exception as e:
                log.error("db_intelligence.first_time_deep_scan_failed", error=str(e))

    threading.Thread(target=_first_time_deep_scan, daemon=True, name="db-intelligence-first-deep-scan").start()
    return fast_ctx



def start_background_refresh() -> None:
    """Start a daemon thread that refreshes the context every REFRESH_HOURS."""
    def _loop():
        # Initial scan on startup (with small delay to let server start)
        time.sleep(5)
        log.info("db_intelligence.initial_scan_starting")
        try:
            get_db_context(force_refresh=False)
            log.info("db_intelligence.initial_scan_complete")
        except Exception as exc:
            log.error("db_intelligence.initial_scan_failed", error=str(exc))

        # Periodic refresh
        while True:
            time.sleep(REFRESH_HOURS * 3600)
            try:
                get_db_context(force_refresh=True)
                log.info("db_intelligence.periodic_refresh_complete")
            except Exception as exc:
                log.error("db_intelligence.periodic_refresh_failed", error=str(exc))

    t = threading.Thread(target=_loop, daemon=True, name="db-intelligence-refresh")
    t.start()
    log.info("db_intelligence.background_refresh_started", interval_hours=REFRESH_HOURS)
