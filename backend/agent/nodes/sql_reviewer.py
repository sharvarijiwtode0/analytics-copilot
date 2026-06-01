"""
Node: SQL DBA Reviewer Agent
============================
Acts as a ClickHouse database administrator. It reviews the generated SQL query
against security, syntax, and business rules before execution. If validation fails,
it generates detailed feedback to correct the SQL.
"""
from __future__ import annotations
import json
import re
import structlog
from backend.agent.state import AnalyticsState
from backend.agent.llm import call_llm

log = structlog.get_logger(__name__)

# Strict rule check on the Python side before hitting LLM (fast, lightweight guardrails)
def _fast_regex_pre_check(sql: str, datasource_id: str, question: str, review_count: int) -> str | None:
    """Perform quick regex syntax & business logic checks."""
    if not sql:
        return "Empty SQL query."
        
    sql_upper = sql.upper().strip()
    sql_lower = sql.lower().strip()
    q_lower = question.lower()
    
    # 1. Safety check
    from backend.agent.nodes.sql_gen import _is_safe_sql
    is_safe, reason = _is_safe_sql(sql)
    if not is_safe:
        return f"Safety violation: {reason}"
        
    # Verify basic SQL syntax - matching parentheses
    if sql.count('(') != sql.count(')'):
        return "ClickHouse syntax error: Mismatched parentheses in query."
        
    # 2. ClickHouse / Limese business rule checks
    if datasource_id == "limese":
        # Rule 1: Exclude cancelled or returned orders when querying combined_sales_final
        if "COMBINED_SALES_FINAL" in sql_upper:
            # Check for final_status filter
            status_match = re.search(r"final_status\s+not\s+in\s*\(", sql_lower)
            if not status_match:
                return (
                    "Business rule violation: You must ALWAYS exclude cancelled or returned orders "
                    "when querying 'combined_sales_final'. Please add: "
                    "final_status NOT IN ('cancelled','Cancelled','CANCELLED','returned','Returned')"
                )
            
            # Exclusion must cover both cancelled and returned
            if "cancelled" not in sql_lower or "returned" not in sql_lower:
                return (
                    "Business rule violation: Exclusion filter for combined_sales_final must cover "
                    "both cancelled and returned statuses. Use: "
                    "final_status NOT IN ('cancelled','Cancelled','CANCELLED','returned','Returned')"
                )
            
        # Rule 2: For revenue/sales queries on combined_sales_final, use row_subtotal, NEVER order_price
        if "combined_sales_final" in sql_lower:
            is_revenue_query = any(w in q_lower for w in ["revenue", "sales", "turnover", "income", "amount", "value"])
            if is_revenue_query:
                if "order_price" in sql_lower and "row_subtotal" not in sql_lower:
                    return (
                        "Business rule violation: For revenue queries on 'combined_sales_final', "
                        "you must use 'row_subtotal' as the revenue column. 'order_price' contains full order values, "
                        "which results in double-counting when grouped by line item."
                    )
                    
        # Rule 3: For units/quantities queries on combined_sales_final, use quantity_ordered, NEVER shipped_qty
        if "combined_sales_final" in sql_lower:
            is_units_query = any(w in q_lower for w in ["quantity", "units", "items", "count", "qty", "volume"])
            if is_units_query:
                if "shipped_qty" in sql_lower:
                    return (
                        "Business rule violation: Use 'quantity_ordered' for ordered units/quantities. "
                        "'shipped_qty' is always 0 in the current schema."
                    )

        # Rule 4: Date Filtering - do not use toYear() or dynamic date functions (timezone issues)
        if "toyear(" in sql_lower or "today(" in sql_lower or "now(" in sql_lower:
            return (
                "Business rule violation: Do not use toYear() or dynamic date functions (like today() or now()). "
                "Instead, apply static date filters such as: date_created >= '2025-01-01'."
            )

        # Rule 6: ClickHouse function checks (lag/lead)
        if re.search(r"\blag\s*\((?!inframe)", sql_lower) or re.search(r"\blead\s*\((?!inframe)", sql_lower):
            return "ClickHouse syntax error: Use lagInFrame() instead of lag(), and leadInFrame() instead of lead()."
            
        # Rule 7: Select Clause Check
        if "row_subtotal" in sql_lower and "quantity_ordered" in sql_lower:
            is_revenue_only = any(w in q_lower for w in ["revenue", "sales"]) and not any(w in q_lower for w in ["quantity", "units", "items", "qty"])
            if is_revenue_only and review_count == 0:
                return "Business rule violation: The query select clause contains 'quantity_ordered' but the user only requested revenue/sales."
            
    return None


async def review_sql(state: AnalyticsState) -> AnalyticsState:
    """
    DBA Review Node.
    Validates SQL query safety, compliance, and correctness locally with zero latency.
    """
    sql = state.get("sql_query", "")
    datasource_id = state.get("datasource_id", "")
    question = state.get("user_question", "")
    
    # Initialize count if not set
    review_count = state.get("review_retry_count", 0)
    
    # Upstream generation error exists, propagate it
    if state.get("error") and not state.get("sql_query"):
        return state
        
    log.info("sql_reviewer.started_local_critic", review_count=review_count, sql_preview=sql[:150])
    
    # Run the comprehensive local DBA compliance critic
    local_err = _fast_regex_pre_check(sql, datasource_id, question, review_count)
    
    if local_err:
        log.warning("sql_reviewer.local_critic_failed", error=local_err)
        return {
            **state,
            "sql_validated": False,
            "dba_feedback": local_err,
            "review_retry_count": review_count + 1,
            "error": f"DBA Review failed: {local_err}"
        }
        
    log.info("sql_reviewer.passed_local_critic")
    return {
        **state,
        "sql_validated": True,
        "dba_feedback": "",
        "error": None
    }
