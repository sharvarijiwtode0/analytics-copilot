"""
Node: Meta-Cognitive Supervisor Agent
=====================================
Acts as the central coordination system (prefrontal cortex) for the copilot.
Inspects the current state, checks execution status/errors, plans next actions,
and dynamically routes to the appropriate agent node.

Architecture: Deterministic routing as primary engine (sub-millisecond).
Optional LLM-assisted routing for ambiguous cases (intent=analytical_question
or sql_retry > 0) for deeper reasoning about next steps.
"""
from __future__ import annotations
import json
import logging
import structlog
from backend.agent.state import AnalyticsState

log = structlog.get_logger(__name__)

# Cases where LLM-assisted routing can provide better decisions
_LLM_ASSIST_CASES = {"analytical_question", "why_question", "complex_comparison"}

_ERROR_LLM_RETRY_THRESHOLD = 1  # Only use LLM for retry after 1+ failures


async def _try_llm_routing(question: str, state: dict) -> str | None:
    """Attempt LLM-assisted routing. Returns next_step or None on failure."""
    from backend.agent.llm import call_llm

    status_lines = []
    if state.get("sql_query"):
        status_lines.append(f"SQL: {state['sql_query'][:200]}")
    if state.get("error"):
        status_lines.append(f"Error: {state['error'][:200]}")
    if state.get("query_results", {}).get("row_count"):
        status_lines.append(f"Rows: {state['query_results']['row_count']}")

    status = "\n".join(status_lines) if status_lines else "No query yet"

    prompt = f"""Based on the current state, decide the SINGLE best next step for this analytics copilot.

Question: "{question}"
Intent: {state.get('intent', {}).get('type', 'unknown')}
{status}

Available steps: understand_intent, discover_schema, generate_sql, review_sql, execute_sql,
analyze_insights, generate_viz_config, review_insights, compose_response, general_llm, disambiguate

If there is a SQL error that might benefit from a different approach (not just retrying),
suggest generate_sql. If the pipeline is complete, suggest compose_response.

Return ONLY the step name, nothing else."""

    try:
        resp = await call_llm(
            messages=[{"role": "user", "content": prompt}],
            task="routing",
            max_tokens=10,
            temperature=0.0,
        )
        step = resp.content.strip().strip().strip("`").strip('"').strip()
        valid_steps = {
            "understand_intent", "discover_schema", "generate_sql", "review_sql",
            "execute_sql", "analyze_insights", "generate_viz_config", "review_insights",
            "compose_response", "general_llm", "disambiguate",
        }
        if step in valid_steps:
            return step
    except Exception:
        pass
    return None


async def supervisor(state: AnalyticsState) -> AnalyticsState:
    """
    Supervisor Agent Node.
    Evaluates current progress and decides the next step to invoke.
    """
    question = state.get("user_question", "")
    intent = state.get("intent", {})
    intent_type = intent.get("type", "")
    schema_context = state.get("schema_context", {})
    sql_query = state.get("sql_query", "")
    sql_validated = state.get("sql_validated", False)
    query_results = state.get("query_results", {})
    insights = state.get("insights", [])
    viz_config = state.get("viz_config", {})
    error = state.get("error")
    
    sql_retry = state.get("sql_retry_count", 0)
    review_retry = state.get("review_retry_count", 0)
    insights_validated = state.get("insights_validated", False)
    critic_retry = state.get("critic_retry_count", 0)
    critic_feedback = state.get("critic_feedback", "")
    
    thoughts = state.get("supervisor_thoughts", [])
    if not isinstance(thoughts, list):
        thoughts = []
        
    log.info(
        "supervisor.evaluating",
        intent=intent_type,
        has_sql=bool(sql_query),
        sql_validated=sql_validated,
        has_results=bool(query_results.get("rows")),
        insights_validated=insights_validated,
        error=error[:80] if error else None
    )

    # Deterministic routing engine — 100% accurate, sub-millisecond
    if state.get("pre_filter_response") or state.get("skip_pipeline"):
        next_step = "compose_response"
        thought_log = "Response is already pre-generated. Routing directly to response composer to finalize."
    elif not intent_type:
        next_step = "understand_intent"
        thought_log = "The Intent Type is currently empty, triggering initial classification."
    elif intent_type in ("greeting", "conversational", "off_topic"):
        next_step = "general_llm"
        thought_log = "Conversational or greeting intent detected, routing to general LLM responder."
    elif intent_type in _LLM_ASSIST_CASES:
        # Analytical/why questions: let LLM decide between full pipeline or direct compose
        llm_step = await _try_llm_routing(question, state)
        next_step = llm_step if llm_step else "discover_schema"
        thought_log = f"Analytical question detected. {'LLM-assisted routing to' if llm_step else 'Default routing to schema discovery.'} step={next_step}"
    elif intent_type == "schema_info":
        next_step = "compose_response"
        thought_log = "Database schema/table description request detected. Routing directly to response composer to present table definitions."
    elif state.get("ambiguity_score", 0.0) > 0.6 and not state.get("locked_tables"):
        next_step = "disambiguate"
        thought_log = "High schema ambiguity detected. Routing to interactive disambiguation modal."
    elif not schema_context.get("relevant_tables"):
        next_step = "discover_schema"
        thought_log = "No schema context loaded. Executing semantic and catalog discover_schema."
    elif not sql_query:
        next_step = "generate_sql"
        thought_log = "No query drafted. Invoking SQL generator node."
    elif not sql_validated:
        if review_retry < 2:
            next_step = "review_sql"
            thought_log = "Query drafted but not validated. Dispatching to DBA SQL Reviewer."
        else:
            next_step = "execute_sql"
            thought_log = "Maximum DBA review attempts reached. Routing directly to SQL Executor."
    elif "columns" not in query_results:
        # Execution has not run yet!
        next_step = "execute_sql"
        thought_log = "SQL validated successfully. Routing to connector for execution."
    elif error and sql_retry < 2:
        # Try LLM-assisted routing for error recovery (may suggest different approach)
        llm_step = await _try_llm_routing(question, state)
        next_step = llm_step if llm_step else "generate_sql"
        thought_log = f"SQL execution failed. {'LLM-assisted' if llm_step else 'Routing back'} to {'LLM suggested' if llm_step else 'SQL Gen'} for auto-fix."
    elif error:
        next_step = "compose_response"
        thought_log = "SQL execution failed and retries exhausted. Preparing error response."
    elif not query_results.get("rows"):
        # Query executed successfully but returned 0 rows!
        next_step = "compose_response"
        thought_log = "Query returned 0 rows. Routing to response composer with zero-data explanation."
        # Store info about why 0 rows so compose_response can explain it
        state["query_results"]["row_count"] = 0
        state["query_results"]["zero_row_context"] = {
            "question": question,
            "intent_type": intent_type,
            "sql_query": sql_query[:300],
            "has_results_structure": bool(query_results.get("columns")),
        }
    elif not viz_config:
        next_step = "generate_viz_config"
        thought_log = "Database query completed successfully. Generating Apache ECharts visualization."
    elif not insights:
        next_step = "analyze_insights"
        thought_log = "Visualization configured. Dispatching query output to Insight Analyst."
    elif not insights_validated:
        if critic_feedback and critic_retry < 2:
            next_step = "analyze_insights"
            thought_log = f"Insight Critic flagged anomalies: {critic_feedback[:80]}. Routing back for analysis regeneration."
        elif not critic_feedback:
            next_step = "review_insights"
            thought_log = "Insights compiled. Dispatching to Critic Agent for mathematical verification."
        else:
            next_step = "compose_response"
            thought_log = "Critic review completed or retries exhausted. Finalizing composition."
    else:
        next_step = "compose_response"
        thought_log = "All pipeline steps completed successfully. Generating final business analyst narrative."

    log.info("supervisor.decision", thought=thought_log, next_step=next_step)
    
    # Append the supervisor thought to the history log (cap at 20 to prevent unbounded growth)
    new_thoughts = (thoughts + [f"Step decision: {next_step}. Reason: {thought_log}"])[:20]
    
    return {
        **state,
        "next_step": next_step,
        "supervisor_thoughts": new_thoughts
    }
