"""
Node 2: Data & Schema Discovery
Finds the relevant tables and columns for the question.
Uses cached schema + LLM to select most relevant tables.

Role boundary: ONLY selects which tables are relevant.
Does NOT generate SQL. Does NOT execute queries.
"""
from __future__ import annotations
import json
import structlog
from backend.agent.state import AnalyticsState
from backend.agent.llm import call_llm
from backend.data.connector import get_schema

log = structlog.get_logger(__name__)

# Keyword → table mapping for instant fallback when LLM JSON fails
_TABLE_KEYWORDS: dict[str, list[str]] = {
    "combined_sales_final":          ["revenue", "sales", "order", "platform", "channel", "subtotal", "amount", "income", "growth", "total sales", "overall revenue"],
    "product_master":                ["product", "item", "sku", "category", "brand", "name", "skincare", "makeup", "haircare", "mrp", "cogs", "margin", "wholesale cost"],
    "product_catlog":                ["catalog", "catlog", "catalogue", "listing"],
    "inventory_sales_overview_new":  ["inventory", "stock", "warehouse", "restock", "supply", "on hand", "low stock", "sell-through", "stock level"],
    "platform_sku_mapping":          ["mapping", "external sku", "platform sku", "sku map"],
    "shopify_orders":                ["shopify", "online store", "website", "storefront", "web order"],
    "unicomm_sales_final":           ["unicomm", "unicommerce", "marketplace"],
    "zoho_sales_final":              ["zoho", "zoho sales", "b2b sales", "distributor invoice"],
    "zoho_purchase_orders":          ["purchase order", "po", "zoho po", "procurement", "supply purchase"],
    "inventory_ledger":              ["ledger", "stock ledger", "inventory ledger", "stock adjustments"],
    "product_hierarchy":             ["hierarchy", "category tree", "taxonomies"],
    "lead_time":                     ["lead time", "supplier delay", "shipment days", "replenishment days"],
}

# 1-2 sentence business purpose descriptions for compact schema selection context
_TABLE_PURPOSES: dict[str, str] = {
    "combined_sales_final":          "Overall sales, revenue, orders, growth, and channel aggregates across Nykaa, POS, Myntra, Shopify, AJIO, direct, POS, and all platforms. Use this for general revenue and sales volume analysis.",
    "product_master":                "Master catalog of products, categories (Skincare, Makeup, Haircare), brand names, MRP, COGS, and margins. Join with sales/inventory tables via internal_sku.",
    "product_catlog":                "Platform SKU mappings and specific listings.",
    "inventory_sales_overview_new":  "Daily inventory snapshots, warehouse stock levels, stock on hand (OOS/low stock), facilities, and sell-through metrics.",
    "platform_sku_mapping":          "Mapping of external platform SKUs to internal SKUs.",
    "shopify_orders":                "Shopify website/online storefront D2C orders only. Use only if Shopify/website is explicitly requested.",
    "unicomm_sales_final":           "Unicommerce marketplace OMS orders and channel shipments.",
    "zoho_sales_final":              "Zoho invoices, B2B wholesale orders, and trade customer sales.",
    "zoho_purchase_orders":          "Zoho Purchase Orders (procurement, supplier orders, incoming receipts).",
    "inventory_ledger":              "Inventory ledger transaction logs showing detailed stock inflows/outflows over time.",
    "product_hierarchy":             "Product tree parent-child relationships for combo/kit/variant products.",
    "lead_time":                     "Supplier lead times, average replenishment delay, and vendor shipment days.",
}


import re

def _keyword_select_tables(question: str, tables: list[dict]) -> list[str]:
    """
    Pick tables dynamically by scoring keyword matches in the question
    against table names, column names, descriptions, and Limese fallbacks,
    then sorting to return the most relevant ones.
    """
    q = question.lower()
    table_scores = {}

    for table in tables:
        tname = table["name"]
        tname_lower = tname.lower()
        score = 0

        # Rule A: Table name match (very high priority)
        if tname_lower in q:
            score += 20
        else:
            for part in re.split(r'[-_]', tname_lower):
                if len(part) > 2 and part in q:
                    score += 15

        # Rule B: Specific Limese keywords (high priority)
        kws = _TABLE_KEYWORDS.get(tname, [])
        for kw in kws:
            if kw in q:
                if kw in ["shopify", "unicomm", "zoho", "nykaa", "myntra"]:
                    score += 18
                else:
                    score += 10

        # Rule C: Column names (medium priority)
        for col in table.get("columns", []):
            cname = col.get("name", "").lower()
            if cname in q:
                score += 5
            else:
                for part in re.split(r'[-_]', cname):
                    if len(part) > 2 and part in q:
                        score += 3

        # Rule D: Description (low priority)
        desc = table.get("description", "").lower()
        if desc:
            for word in re.findall(r'\b\w{3,}\b', desc):
                if word in q:
                    score += 2

        if score > 0:
            table_scores[tname] = score

    # Sort tables by score descending
    sorted_tables = sorted(table_scores.items(), key=lambda x: x[1], reverse=True)
    selected = [t[0] for t in sorted_tables]

    # Always ensure a default table is selected if list is empty
    if not selected and tables:
        selected.append(tables[0]["name"])

    return selected[:4]


async def discover_schema(state: AnalyticsState) -> AnalyticsState:
    # Skip if SQL was already populated by semantic cache
    if state.get("sql_query"):
        return state

    intent = state.get("intent", {})
    datasource_id = state.get("datasource_id")
    question = intent.get("rephrased_question") or state.get("user_question", "")

    # Fetch schema (from 1-hour in-memory cache or live)
    try:
        full_schema = await get_schema(datasource_id)
    except Exception as exc:
        log.error("schema.fetch_failed", error=str(exc))
        full_schema = {"tables": [], "error": str(exc)}

    tables = full_schema.get("tables", [])
    all_table_names = [t["name"] for t in tables]

    if not tables:
        return {**state, "schema_context": {"relevant_tables": [], "error": "No tables found"}}

    # Build a COMPACT schema summary — includes table name, its high-signal business purpose, and column names
    schema_summary = "\n".join(
        f"- {t['name']} ({_TABLE_PURPOSES.get(t['name'], t.get('description') or 'Operational dataset')}) - Columns: {', '.join(c['name'] for c in t.get('columns', [])[:20])}"
        for t in tables[:20]
    )

    prompt = f"""Select the most relevant database tables for this question.

Question: "{question}"

Available tables and their columns:
{schema_summary}

Return JSON only — no explanation:
{{
  "relevant_tables": ["table1", "table2"],
  "suggested_joins": ["table_a.col = table_b.col"]
}}

Rules:
- Return at most 4 table names (strings only, not objects)
- Always include combined_sales_final for revenue/sales/order questions
- Include product_master when products/categories/SKUs are mentioned
- Include inventory_sales_overview_new for stock/inventory questions"""

    # Use selected tables: 
    # (a) Locked tables from clarification
    # (b) Top candidate tables from semantic scoring
    # (c) Fallback to keyword matching
    selected_names = []
    if state.get("locked_tables"):
        selected_names = state["locked_tables"]
    elif state.get("candidate_tables"):
        # Select positive scoring tables
        selected_names = [t["name"] for t in state["candidate_tables"] if t.get("score", 0.0) > 0.0]

    if not selected_names:
        selected_names = _keyword_select_tables(question, tables)

    # Record table access in user history to learn user profile affinities
    try:
        from backend.services.user_history import record_table_access
        record_table_access(
            user_id=state.get("user_id", "anonymous"),
            table_names=selected_names,
            session_id=state.get("session_id")
        )
    except Exception as exc:
        log.warning("schema.record_history_failed", error=str(exc))

    suggested_joins = []

    # Enrich with full column metadata from the real schema
    table_map = {t["name"]: t for t in tables}
    relevant_tables = []
    for name in selected_names[:4]:
        full_table = table_map.get(name, {"name": name, "columns": []})
        relevant_tables.append({
            "name": name,
            "columns": full_table.get("columns", []),
            "sample_data": full_table.get("sample_data"),
            "row_count": full_table.get("row_count"),
            "description": full_table.get("description", ""),
        })

    schema_context = {
        "relevant_tables": relevant_tables,
        "suggested_joins": suggested_joins,
        "all_tables": all_table_names,
    }

    log.info("schema.discovered", tables=[t["name"] for t in relevant_tables])
    return {**state, "schema_context": schema_context}
