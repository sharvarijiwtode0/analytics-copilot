"""
Semantic Table Catalog Service.
Defines domains, priorities, and semantic blueprints for all 173 ClickHouse tables.
Uses a hybrid-dynamic scoring engine to rank tables in under 2ms.
"""
from __future__ import annotations
import re
import structlog

log = structlog.get_logger(__name__)

# Business domains in Limese brand operations
DOMAINS = {
    "core": "Global brand sales aggregations and overall summaries",
    "shopify": "Direct-to-consumer (D2C) web storefront orders and customers",
    "zoho": "Zoho ERP invoice bookings, purchase orders, and corporate finance",
    "unicomm": "Unicommerce logistics, B2B distribution, dispatches, and warehouse inventory",
    "nykaa": "Nykaa beauty marketplace sales, purchase orders, and returns",
    "myntra": "Myntra fashion marketplace sales",
    "flipkart": "Flipkart marketplace sales and listing details",
    "reliance": "Reliance channels (Ajio online, Tira offline/online, Azorte online)",
    "inventory": "Inventory movement, ledger, ageing, and stock level snapshots",
    "catalog": "Product hierarchies, SKU mappings, and listings metadata",
    "staging": "Temporary raw ingestion, sync status, and internal pipeline tables"
}

# Core priority table blueprints (golden datasets)
PRIORITY_BLUEPRINTS = {
    "combined_sales_final": {
        "business_purpose": "Global brand sales aggregate across all retail channels (Shopify + Zoho + Unicommerce). Use for overall revenue, units, and brand-level growth.",
        "domain": "core",
        "keywords": ["total sales", "overall sales", "brand revenue", "overall growth", "global sales", "all platforms"],
        "priority": "high",
        "data_quality": "golden"
    },
    "product_master": {
        "business_purpose": "Master lookup for product details, names, category hierarchies, and product COGS/margins.",
        "domain": "catalog",
        "keywords": ["product details", "item name", "category", "cogs", "product margin", "brand name", "mrp"],
        "priority": "high",
        "data_quality": "golden"
    },
    "product_catlog": {
        "business_purpose": "Active catalog listings and retail storefront mappings.",
        "domain": "catalog",
        "keywords": ["catalog", "catlog", "listings", "storefront listings"],
        "priority": "high",
        "data_quality": "golden"
    },
    "inventory_sales_overview_new": {
        "business_purpose": "Daily inventory balance snapshots and gross sales. Use for current stock levels and daily sell-through rates.",
        "domain": "inventory",
        "keywords": ["stock levels", "current stock", "burn rate", "units on hand", "inventory balance", "gross sales rs"],
        "priority": "high",
        "data_quality": "golden"
    },
    "platform_sku_mapping": {
        "business_purpose": "Maps external platform SKU identifiers to internal product SKUs.",
        "domain": "catalog",
        "keywords": ["sku mapping", "external sku", "platform sku", "sku code"],
        "priority": "high",
        "data_quality": "golden"
    },
    "shopify_orders": {
        "business_purpose": "Direct-to-consumer (D2C) web storefront orders. Use for web-only sales, shopify discount codes, and abandoned cart analysis.",
        "domain": "shopify",
        "keywords": ["shopify orders", "website sales", "online store", "storefront discounts", "abandoned checkout"],
        "priority": "high",
        "data_quality": "golden"
    },
    "unicomm_sales_final": {
        "business_purpose": "Unicommerce logistics, bulk dispatches, and warehouse-level sales.",
        "domain": "unicomm",
        "keywords": ["unicomm sales", "unicommerce orders", "warehouse sales", "dispatch orders", "logistics sales"],
        "priority": "high",
        "data_quality": "golden"
    },
    "zoho_sales_final": {
        "business_purpose": "Zoho ERP sales invoice bookings. Use for official finance, offline B2B client bookings, and Zoho invoices.",
        "domain": "zoho",
        "keywords": ["zoho sales", "zoho invoices", "erp bookings", "b2b client", "finance sales"],
        "priority": "high",
        "data_quality": "golden"
    },
    "zoho_purchase_orders": {
        "business_purpose": "Vendor purchase orders, inventory arrivals, and supplier rates.",
        "domain": "zoho",
        "keywords": ["purchase order", "po", "supplier purchase", "vendor po", "replenishment rates"],
        "priority": "high",
        "data_quality": "golden"
    },
    "inventory_ledger": {
        "business_purpose": "Historical inventory level entries and movements.",
        "domain": "inventory",
        "keywords": ["inventory history", "stock history", "ledger entries", "historic stock"],
        "priority": "high",
        "data_quality": "golden"
    },
    "product_hierarchy": {
        "business_purpose": "Taxonomy mapping for Brand, Collection, and Category Level levels.",
        "domain": "catalog",
        "keywords": ["product hierarchy", "collection", "taxonomy", "brand grouping"],
        "priority": "high",
        "data_quality": "golden"
    },
    "lead_time": {
        "business_purpose": "Supplier fulfillment lead time and shipment latency.",
        "domain": "inventory",
        "keywords": ["lead time", "replenishment latency", "delivery days", "fulfillment delay"],
        "priority": "high",
        "data_quality": "golden"
    }
}


def get_table_blueprint(table_name: str) -> dict:
    """Get blueprint for any table (explicit or auto-generated fallback)."""
    if table_name in PRIORITY_BLUEPRINTS:
        return PRIORITY_BLUEPRINTS[table_name]

    # Auto-generate dynamic blueprint by parsing table name prefix/structure
    name_lower = table_name.lower()

    # Determine domain & keywords dynamically
    domain = "staging"
    priority = "low"
    data_quality = "operational"

    if name_lower.startswith("shopify"):
        domain = "shopify"
        priority = "medium"
    elif name_lower.startswith("zoho"):
        domain = "zoho"
        priority = "medium"
    elif name_lower.startswith("unicomm") or name_lower.startswith("unicommerce"):
        domain = "unicomm"
        priority = "medium"
    elif name_lower.startswith("nykaa"):
        domain = "nykaa"
        priority = "medium"
    elif name_lower.startswith("myntra"):
        domain = "myntra"
        priority = "medium"
    elif name_lower.startswith("flipkart"):
        domain = "flipkart"
        priority = "medium"
    elif name_lower.startswith("reliance") or "ajio" in name_lower or "tira" in name_lower:
        domain = "reliance"
        priority = "medium"
    elif "inventory" in name_lower or "stock" in name_lower or "ledger" in name_lower:
        domain = "inventory"
        priority = "medium"
    elif "catalog" in name_lower or "catlog" in name_lower or "sku" in name_lower:
        domain = "catalog"
        priority = "medium"
    elif "sales" in name_lower or "orders" in name_lower:
        domain = "core"
        priority = "medium"

    # Mark internal temp, archive, backup, test tables as low priority staging
    if any(tag in name_lower for tag in ["archive", "temp", "test", "backup", "old", "raw", "ingress"]):
        domain = "staging"
        priority = "low"
        data_quality = "archive"

    # Split keywords from table name parts
    keywords = [part for part in re.split(r'[-_.]', name_lower) if len(part) > 2]
    # Add domain specific descriptors
    keywords.append(domain)

    return {
        "business_purpose": f"Operational dataset related to {domain} Limese channel.",
        "domain": domain,
        "keywords": list(set(keywords)),
        "priority": priority,
        "data_quality": data_quality
    }


def score_tables(question: str, user_id: str = "anonymous", history_profile: dict[str, float] | None = None) -> list[dict]:
    """
    Scores all tables against user's question, applying user domain history boosts.
    Returns ranked tables with metadata.
    """
    from backend.data.connector import SCHEMA_CACHE_FILE
    import json

    q = question.lower()
    ranked = []

    # Get the list of all available tables from schema cache or priority list
    tables = list(PRIORITY_BLUEPRINTS.keys())
    if SCHEMA_CACHE_FILE.exists():
        try:
            with open(SCHEMA_CACHE_FILE, "r") as f:
                data = json.load(f)
            limese_data = data.get("limese", {}).get("schema", {}).get("tables", [])
            for t in limese_data:
                if t.get("name") not in tables:
                    tables.append(t["name"])
        except Exception:
            pass

    for table in tables:
        bp = get_table_blueprint(table)
        score = 0.0

        # 1. Direct table name match in question
        if table in q or table.replace("_", " ") in q:
            score += 10.0

        # 2. Match semantic keywords
        for kw in bp["keywords"]:
            if kw in q:
                score += 2.0
                # Exact word boundaries boost
                if re.search(r'\b' + re.escape(kw) + r'\b', q):
                    score += 1.5

        # 3. Boost priority tables (golden records) to prefer clean data
        if bp["priority"] == "high":
            score += 1.5
        elif bp["priority"] == "medium":
            score += 0.5

        # 4. User profile history domain boost
        if history_profile and bp["domain"] in history_profile:
            boost = history_profile[bp["domain"]]
            score += boost * 2.0

        # 5. Penalize raw staging/backup tables slightly unless explicitly asked
        if bp["data_quality"] == "archive":
            score -= 2.0
        elif bp["domain"] == "staging":
            score -= 1.0

        if score > 0:
            ranked.append({
                "name": table,
                "score": round(score, 2),
                "domain": bp["domain"],
                "data_quality": bp["data_quality"],
                "priority": bp["priority"],
                "description": bp["business_purpose"]
            })

    # Sort descending by score, tie-breaker: high priority first
    ranked.sort(key=lambda x: (-x["score"], 0 if x["priority"] == "high" else 1))
    return ranked
