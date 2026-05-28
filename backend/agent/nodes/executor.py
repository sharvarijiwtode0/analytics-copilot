"""
Node 4: Query Execution
Runs the validated SQL against the connected datasource.
Handles errors gracefully. On ClickHouse identifier errors, attempts one
automatic SQL fix via LLM before giving up.

Role boundary: ONLY executes SQL and returns raw results.
Does NOT generate insights. Does NOT build charts.
"""
from __future__ import annotations
import time
import structlog
from backend.agent.state import AnalyticsState
from backend.data.connector import execute_query
from backend.config import settings

log = structlog.get_logger(__name__)

# ClickHouse error patterns that indicate fixable SQL problems
_FIXABLE_ERRORS = [
    "unknown_identifier",
    "missing columns",
    "no such column",
    "unknown function",
    "syntax error",
]


def _is_fixable(error_msg: str) -> bool:
    el = error_msg.lower()
    return any(p in el for p in _FIXABLE_ERRORS)


async def _try_fix_sql(sql: str, error_msg: str, question: str, datasource_id: str) -> str | None:
    """
    Ask the LLM to fix a broken SQL query given the ClickHouse error.
    Returns corrected SQL string or None if fix failed.
    """
    from backend.agent.llm import call_llm
    from backend.services.db_intelligence import get_db_context, build_sql_context_prompt

    try:
        ctx = get_db_context()
        schema_hint = build_sql_context_prompt(ctx, question, ["combined_sales_final", "product_master"])
    except Exception:
        schema_hint = ""

    prompt = f"""A ClickHouse SQL query failed with this error. Fix it.

Original question: "{question}"

Broken SQL:
{sql}

ClickHouse error:
{error_msg[:500]}

{schema_hint}

CRITICAL RULES:
- ALL table aliases MUST be declared in FROM/JOIN clause before using them.
  Example: "FROM combined_sales_final csf" then use "csf.date_created"
- Use lagInFrame() instead of lag(), leadInFrame() instead of lead()
- Date filter: date_created >= '2025-01-01'
- Revenue: row_subtotal | Units: quantity_ordered
- Always: WHERE final_status NOT IN ('cancelled','Cancelled','CANCELLED','returned','Returned')

Return ONLY this JSON:
{{
  "sql": "<corrected complete SQL query>",
  "fix_applied": "<one sentence describing what you fixed>"
}}"""

    try:
        resp = await call_llm(
            messages=[{"role": "user", "content": prompt}],
            task="sql",
            max_tokens=700,
            temperature=0.0,
        )
        import json, re
        raw = resp.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()
        parsed = json.loads(raw)
        fixed_sql = parsed.get("sql", "").strip()
        fix_desc = parsed.get("fix_applied", "")
        if fixed_sql and "SELECT" in fixed_sql.upper() and "FROM" in fixed_sql.upper():
            log.info("executor.sql_fixed", fix=fix_desc[:100])
            return fixed_sql
    except Exception as exc:
        log.warning("executor.fix_failed", error=str(exc)[:100])
    return None


async def execute_sql(state: AnalyticsState) -> AnalyticsState:
    sql = state.get("sql_query", "")
    datasource_id = state.get("datasource_id")

    # Propagate upstream errors (e.g. SQL gen failure)
    if state.get("error"):
        return state

    if not sql:
        return {**state, "error": "No SQL query to execute", "query_results": {"rows": [], "columns": [], "row_count": 0}}

    async def _run_query(query: str) -> tuple[dict | None, str | None]:
        """Run query, return (result, error_msg)."""
        try:
            t0 = time.perf_counter()
            result = await execute_query(datasource_id, query, timeout=settings.query_timeout_seconds)
            execution_ms = int((time.perf_counter() - t0) * 1000)
            rows = result.get("rows", [])
            columns = result.get("columns", [])
            log.info("sql.executed", rows=len(rows), columns=len(columns), execution_ms=execution_ms)
            return {
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "execution_time_ms": execution_ms,
                "truncated": len(rows) >= settings.max_rows_returned,
            }, None
        except Exception as exc:
            return None, str(exc)

    # First attempt
    query_results, error_msg = await _run_query(sql)

    if query_results is not None:
        return {**state, "query_results": query_results, "error": None}

    log.error("sql.execution_failed", error=error_msg[:200], sql=sql[:150])

    # Attempt automatic fix if ClickHouse and error is fixable
    if datasource_id == "limese" and error_msg and _is_fixable(error_msg):
        log.info("executor.attempting_auto_fix", error=error_msg[:100])
        fixed_sql = await _try_fix_sql(sql, error_msg, state.get("user_question", ""), datasource_id)
        if fixed_sql:
            log.info("executor.auto_fix_succeeded", fixed_sql=fixed_sql[:200])
            query_results, error_msg_fixed = await _run_query(fixed_sql)
            if query_results is not None:
                return {
                    **state,
                    "sql_query": fixed_sql,
                    "query_results": query_results,
                    "error": None,
                }
            error_msg = f"{error_msg} (Auto-fix failed with: {error_msg_fixed})"

    # Increment retry count and save error in state to route back
    retry_count = state.get("sql_retry_count", 0)
    return {
        **state,
        "error": f"Query failed: {error_msg}",
        "sql_retry_count": retry_count + 1,
        "query_results": {"rows": [], "columns": [], "row_count": 0},
    }
