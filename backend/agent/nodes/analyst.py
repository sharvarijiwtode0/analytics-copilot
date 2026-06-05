"""
Node 5: Insight & Analysis
Analyzes query results to find key metrics, trends, anomalies.
Generates human-readable insights from raw data.
"""
from __future__ import annotations
import json
import structlog
from backend.agent.state import AnalyticsState
from backend.agent.llm import call_llm

log = structlog.get_logger(__name__)


def _compute_basic_stats(rows: list, columns: list) -> dict:
    """Compute basic stats client-side to reduce LLM tokens."""
    if not rows or not columns:
        return {}

    # Columns that should NEVER be treated as numeric metrics
    NON_METRIC_COLUMNS = {
        "internal_sku", "external_sku", "sku", "product_id", "item_id", "order_id",
        "id", "customer_id", "user_id", "platform_id", "category_id", "brand_id",
        "parent_id", "ref_id", "reference_id", "code", "pincode", "zipcode"
    }

    stats: dict = {}
    # Find numeric columns
    for col in columns:
        # Skip ID/SKU columns - these are identifiers, not metrics
        if any(id_col in col.lower() for id_col in NON_METRIC_COLUMNS):
            continue

        values = []
        for row in rows:
            val = row.get(col) if isinstance(row, dict) else None
            if val is not None:
                try:
                    values.append(float(val))
                except (ValueError, TypeError):
                    pass

        if values:
            stats[col] = {
                "min": min(values),
                "max": max(values),
                "avg": sum(values) / len(values),
                "total": sum(values),
                "count": len(values),
            }

    return stats


def _generate_rule_based_fallback_insights(rows: list, columns: list, basic_stats: dict, question: str) -> dict:
    """Generate high-quality business insights dynamically client-side when the LLM is rate-limited."""
    insights = []
    key_metrics = {}
    
    def local_fmt(val, is_money_metric):
        abs_val = abs(val)
        sign = "-" if val < 0 else ""
        prefix = "₹" if is_money_metric else ""
        if abs_val >= 1_00_00_000:
            return f"{sign}{prefix}{(abs_val / 1_00_00_000):.2f} Cr"
        elif abs_val >= 1_00_000:
            return f"{sign}{prefix}{(abs_val / 1_00_000):.2f} L"
        elif abs_val >= 1_000:
            return f"{sign}{prefix}{(abs_val / 1_000):.1f}K"
        return f"{sign}{prefix}{abs_val:,.2f}"

    # 1. Populate key metrics dynamically (only meaningful business metrics)
    # Skip ID/SKU columns and only include actual business metrics
    NON_METRIC_PATTERNS = ["sku", "id", "_id", "code", "pincode", "zipcode"]

    for col, stats in basic_stats.items():
        col_lower = col.lower()

        # Skip non-metric columns
        if any(pattern in col_lower for pattern in NON_METRIC_PATTERNS):
            continue

        # Only include columns that are clearly business metrics
        is_money = any(kw in col_lower for kw in ["revenue", "sales", "price", "subtotal", "amount", "income", "value", "profit", "cost", "mrp"])
        is_count = any(kw in col_lower for kw in ["quantity", "units", "count", "orders", "inventory", "stock", "volume"])

        if is_money or is_count:
            key_metrics[col] = local_fmt(stats.get("total", 0), is_money)

    # 2. Derive dynamic bullet point insights
    str_cols = []
    num_cols = list(basic_stats.keys())
    for col in columns:
        if col not in num_cols:
            str_cols.append(col)

    # Peak finding
    if str_cols and num_cols:
        dim_col = str_cols[0]
        val_col = num_cols[0]
        is_money = any(kw in val_col.lower() for kw in ["revenue", "sales", "price", "subtotal", "amount", "income", "value"])
        
        peak_row = None
        peak_val = -float("inf")
        for r in rows:
            try:
                v = float(r.get(val_col, 0))
                if v > peak_val:
                    peak_val = v
                    peak_row = r
            except Exception:
                pass
                
        if peak_row and peak_val > -float("inf"):
            peak_dim = peak_row.get(dim_col, "Unknown")
            unit_str = "revenue" if is_money else "units/volume"
            insights.append(f"Peak {unit_str} occurred in **{peak_dim}** at **{local_fmt(peak_val, is_money)}**.")

    # Growth rate finding
    if len(rows) >= 2 and num_cols and str_cols and any(ti in str_cols[0].lower() for ti in ["month", "date", "year", "day", "period"]):
        dim_col = str_cols[0]
        val_col = num_cols[0]
        is_money = any(kw in val_col.lower() for kw in ["revenue", "sales", "price", "subtotal", "amount", "income", "value"])
        try:
            start_val = float(rows[0].get(val_col, 0))
            end_val = float(rows[-1].get(val_col, 0))
            if start_val > 0:
                growth = ((end_val - start_val) / start_val) * 100
                direction = "growth" if growth >= 0 else "decline"
                insights.append(f"Trend shows an overall **{abs(growth):.1f}% {direction}** from {rows[0].get(dim_col)} to {rows[-1].get(dim_col)}.")
        except Exception:
            pass

    # Category comparison insight if category_l1 is present
    if "category_l1" in columns and num_cols:
        cats = list(set(r.get("category_l1") for r in rows if r.get("category_l1")))
        if len(cats) >= 2:
            cat_sums = {}
            val_col = num_cols[0]
            is_money = any(kw in val_col.lower() for kw in ["revenue", "sales", "price", "subtotal", "amount", "income", "value"])
            for r in rows:
                c = r.get("category_l1")
                if c:
                    try:
                        cat_sums[c] = cat_sums.get(c, 0) + float(r.get(val_col, 0))
                    except Exception:
                        pass
            if cat_sums:
                sorted_cats = sorted(cat_sums.items(), key=lambda x: x[1], reverse=True)
                insights.append(f"**{sorted_cats[0][0]}** leads performance with a total of **{local_fmt(sorted_cats[0][1], is_money)}**, followed by **{sorted_cats[1][0]}** at **{local_fmt(sorted_cats[1][1], is_money)}**.")

    # General row count backup
    insights.append(f"Retrieved a total of **{len(rows)}** records matching your query.")

    return {
        "insights": insights,
        "key_metrics": key_metrics,
        "anomalies": [],
        "summary_sentence": f"Found {len(rows)} records.",
    }


async def analyze_insights(state: AnalyticsState) -> AnalyticsState:
    query_results = state.get("query_results", {})
    intent = state.get("intent", {})
    question = state["user_question"]

    rows = query_results.get("rows", [])
    columns = query_results.get("columns", [])

    if not rows:
        return {
            "insights": ["No data found for this query."],
            "key_metrics": {},
            "anomalies": []
        }

    # Compute stats locally
    basic_stats = _compute_basic_stats(rows, columns)

    # Prepare data sample for LLM (send up to 30 rows for better pattern recognition)
    sample_rows = rows[:50]
    data_preview = json.dumps({"columns": columns, "rows": sample_rows[:30]}, default=str)

    stats_summary = json.dumps(basic_stats, indent=2, default=str) if basic_stats else "No numeric columns"

    critic_feedback = state.get("critic_feedback", "")
    critic_warning = ""
    if critic_feedback:
        critic_warning = f"""
⚠️ WARNING: YOUR PREVIOUS INSIGHTS WERE REJECTED BY THE CRITIC AGENT FOR THE FOLLOWING REASONS:
"{critic_feedback}"

You MUST regenerate the insights and fix these factual or mathematical errors. Double-check your numbers against the raw database rows.
"""

    prompt = f"""You are a data analyst. Analyze this query result and generate business insights.
{critic_warning}
Original question: "{question}"
Intent: {intent.get("type")} | Time range: {intent.get("time_range")}

Data stats:
{stats_summary}

Sample data (first 10 rows):
{data_preview}

Total rows: {query_results.get("row_count", 0)}

Return JSON:
{{
  "insights": [
    "<insight 1 — most important finding>",
    "<insight 2>",
    "<insight 3 — optional>"
  ],
  "key_metrics": {{
    "<metric name>": "<value with unit>"
  }},
  "anomalies": [
    "<anomaly or outlier if found>"
  ],
  "trend": "<increasing|decreasing|stable|volatile|null>",
  "top_performer": "<highest value entity if applicable>",
  "bottom_performer": "<lowest value entity if applicable>",
  "summary_sentence": "<one sentence that directly answers the user's question>"
}}

Keep insights actionable and business-focused. Use specific numbers from the data.

CRITICAL REASONING, VISUAL CORRELATION & CONTENT RULES:
1. DEEP CAUSAL ANALYSIS: Do NOT just read out or repeat the raw numbers from the data. That is useless. You must analyze the "why" behind the numbers. Identify contributing factors (e.g. category-specific performance, SKU patterns, platform-specific differences, or seasonal shifts) from the data columns, and provide highly reasonable, logical business explanations/justifications (such as stockouts, marketing/promotional campaigns, pricing shifts, or operational delays).
2. VISUAL CORRELATION: Explicitly correlate your findings with the visualization. Point out what the user should look for in the chart (e.g. "You can see a sharp peak in November in the chart...", or "The visualization clearly shows a downward trend for beauty products...").
3. TREND & ANOMALY REASONING: You MUST analyze the trend's shape: identify the peak, trough, overall slope (growth or decline), and highlight significant month-over-month or period-over-period changes. Call out anomalies or sudden drops as points of interest and justify their occurrence.
4. CAUSES & DRIVERS: If the user asks for "reasons", "causes", "factors", or "drivers" of a trend (decline/growth/change):
   - Every single bullet point in the "insights" list MUST be a specific, concrete reason, factor, or driver supported by the data sample.
   - Address the "why" directly using comparative segments (e.g. comparing the top category vs. declining ones).

IMPORTANT: Use ₹ (Rupee) symbol ONLY for monetary values (Revenue, Sales, Spend, Profit).
Do NOT use ₹ for counts (Orders, Units, Users, Tickets).
Format large numbers in Indian style: e.g. ₹23.8 Cr (monetary), 2.8 L units (count). Do NOT use markdown bolding (**) anywhere."""

    try:
        resp = await call_llm(
            messages=[{"role": "user", "content": prompt}],
            task="analysis",
            max_tokens=600,
            temperature=0.2,
        )
        import re
        raw = resp.content.strip()
        if not raw:
            raise ValueError("Empty LLM response")
        
        # Robust regex-based JSON extraction to isolate the target object
        json_match = re.search(r'(\{.*\})', raw, re.DOTALL)
        raw_json = json_match.group(1) if json_match else raw
        
        if "```" in raw_json:
            raw_json = raw_json.split("```")[1].replace("json", "").strip()
        analysis = json.loads(raw_json)
    except Exception as exc:
        log.warning("analyst.parse_failed_using_dynamic_fallback", error=str(exc))
        analysis = _generate_rule_based_fallback_insights(rows, columns, basic_stats, question)

    cleaned_insights = []
    for ins in analysis.get("insights", []):
        s = str(ins).replace('"', '')
        cleaned_insights.append(s)

    log.info("analyst.complete", insight_count=len(cleaned_insights))
    return {
        "insights": cleaned_insights,
        "key_metrics": analysis.get("key_metrics", {}),
        "anomalies": analysis.get("anomalies", []),
    }
