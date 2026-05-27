"""
Node 2.2: Clarification and Disambiguation.
Intercepts ambiguous requests and presents choices via suggestions,
and resolves follow-up selections dynamically.
"""
from __future__ import annotations
import structlog
from backend.agent.state import AnalyticsState

log = structlog.get_logger(__name__)

# Map friendly option phrases directly to table names for instant lock
OPTION_TO_TABLE_MAP = {
    "show shopify storefront sales": "shopify_orders",
    "show zoho invoices sales": "zoho_sales_final",
    "show combined total sales": "combined_sales_final",
    "query shopify storefront orders": "shopify_orders",
    "query zoho sales invoices": "zoho_sales_final",
    "query combined brand sales": "combined_sales_final",
}


async def disambiguate(state: AnalyticsState) -> AnalyticsState:
    question = state.get("user_question", "").lower().strip()
    history = state.get("conversation_history", [])

    # ─── STEP 1: RESOLVE A PREVIOUS CLARIFICATION CLICK ────────
    # Check if this question is a click on one of our generated suggestion chips
    for phrase, table in OPTION_TO_TABLE_MAP.items():
        if phrase in question or question == phrase:
            log.info("disambiguation.resolved_by_selection", phrase=phrase, table=table)
            state["locked_tables"] = [table]
            state["ambiguity_score"] = 0.0
            state["skip_pipeline"] = False
            return state

    # Check if locked_tables is already set (by pass-through)
    if state.get("locked_tables"):
        return state

    # ─── STEP 2: RUN AMBIGUITY CHECK AND SUGGEST OPTIONS ────────
    ambiguity_score = state.get("ambiguity_score", 0.0)
    candidates = state.get("candidate_tables", [])

    if ambiguity_score > 0.6 and len(candidates) >= 2:
        log.info("disambiguation.triggered", query=question, top_candidate=candidates[0]["name"])

        # Determine which sources are ambiguous
        options_text = []
        follow_up_suggestions = []

        # Find if we have shopify, zoho, and combined candidates in our list
        has_shopify = any(t["name"] == "shopify_orders" for t in candidates[:5])
        has_zoho = any(t["name"] == "zoho_sales_final" for t in candidates[:5])
        has_combined = any(t["name"] == "combined_sales_final" for t in candidates[:5])

        if has_shopify or "shopify" in question:
            options_text.append("• **Shopify Storefront** (Direct-to-consumer online web orders)")
            follow_up_suggestions.append("Show Shopify Storefront Sales")

        if has_zoho or "zoho" in question:
            options_text.append("• **Zoho ERP** (Official accounting invoices and B2B orders)")
            follow_up_suggestions.append("Show Zoho Invoices Sales")

        if has_combined or any(w in question for w in ["total", "combined", "all"]):
            options_text.append("• **Combined View** (Overall summary aggregate across all channels)")
            follow_up_suggestions.append("Show Combined Total Sales")

        # Fallback suggestions from top candidates if no match
        if not follow_up_suggestions:
            for cand in candidates[:3]:
                name_clean = cand["name"].replace("_", " ").title()
                options_text.append(f"• **{name_clean}** ({cand['description']})")
                follow_up_suggestions.append(f"Query {name_clean}")

        bullet_points = "\n".join(options_text)
        clarification_msg = (
            f"I found multiple data sources matching your request for **'{state.get('user_question')}'**.\n\n"
            f"To give you the most accurate answer, which source would you like to query?\n\n"
            f"{bullet_points}\n\n"
            "Please click one of the options below or specify which channel you want."
        )

        # Halt graph execution by returning the response directly
        state["skip_pipeline"] = True
        state["pre_filter_response"] = {
            "text": clarification_msg,
            "chart": None,
            "insights": [],
            "key_metrics": {},
            "sql": "",
            "row_count": 0,
            "viz_type": None,
            "follow_up_questions": follow_up_suggestions,
            "total_latency_ms": 10,
        }
        return state

    return state
