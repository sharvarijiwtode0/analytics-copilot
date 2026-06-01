"""
Node: Meta-Cognitive Supervisor Agent
=====================================
Acts as the central coordination system (prefrontal cortex) for the copilot.
Inspects the current state, checks execution status/errors, plans next actions,
and dynamically routes to the appropriate agent node.
"""
from __future__ import annotations
import json
import re
import structlog
from backend.agent.state import AnalyticsState
from backend.agent.llm import call_llm

log = structlog.get_logger(__name__)

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

    # Build detailed context of current execution status
    status_summary = f"""
Current Execution State:
- Original Question: "{question}"
- Intent Type: "{intent_type}"
- Ambiguity Score: {state.get("ambiguity_score", 0.0)}
- Schema Context: {"Loaded" if schema_context.get("relevant_tables") else "Empty"}
- SQL Query: {"Generated" if sql_query else "None"}
- SQL Validated: {sql_validated}
- Query Results: {query_results.get("row_count", 0)} rows returned
- Insights Extracted: {len(insights)} items
- Insights Validated by Critic: {insights_validated}
- Critic Feedback: "{critic_feedback or 'None'}"
- Visualization Ready: {bool(viz_config)}
- Current Error Status: "{error or 'None'}"
- Retries Count: SQL executions={sql_retry}/2, DBA reviews={review_retry}/2, Critic loops={critic_retry}/2
"""

    prompt = f"""You are the Meta-Cognitive Supervisor Agent (the brain) for an SQL analytics copilot system.
Your job is to read the current execution state, reflect on what has been accomplished, identify errors or gaps, and determine the next logical step in the cognitive reasoning loop.

{status_summary}

DECISION ROUTING RULES (Follow strictly):
1. INITIAL STATE: If "Intent Type" is empty, route to "understand_intent".
2. CONVERSATIONAL/OFF-TOPIC: If "Intent Type" is "greeting", "conversational", or "off_topic", route to "general_llm".
3. CLARIFICATION: If the "Ambiguity Score" is > 0.6 and we have not resolved it, route to "disambiguate".
4. SCHEMA: If "Intent Type" is a data query but "Schema Context" is Empty, route to "discover_schema".
5. SQL GENERATION: If we have "Schema Context" but no "SQL Query" exists, route to "generate_sql".
6. DBA REVIEW: If we have a "SQL Query" but "SQL Validated" is false, and there is NO active database execution error, route to "review_sql".
7. RETRY LOOP (DBA Review failed): If "SQL Validated" is false, we have DBA feedback/error, and "DBA reviews" < 2, route to "generate_sql" to fix it.
8. EXECUTION: If we have a "SQL Query" and "SQL Validated" is true, but "Query Results" is empty and there is no execution error, route to "execute_sql".
9. RETRY LOOP (SQL Execution failed): If "Query Results" has failed (e.g. database error) and "SQL executions" < 2, route to "generate_sql" to fix it.
10. VIZ/INSIGHTS PARALLEL: If "Query Results" has rows (> 0), but either "Insights Extracted" is 0 or "Visualization Ready" is false:
    - Route to "generate_viz_config" if viz_config is empty.
    - Route to "analyze_insights" if insights is empty.
11. INSIGHT CRITIC REVIEW: If "Insights Extracted" is (> 0) but "Insights Validated by Critic" is false, and there is no active execution error:
    - If critic_feedback is present and "Critic loops" < 2 -> route to "analyze_insights" to regenerate/fix.
    - If critic_feedback is empty (not yet reviewed) -> route to "review_insights".
12. RESPONSE COMPOSITION: If all required data has been retrieved/analyzed, or if we have hit max retries, or if the intent was general, route to "compose_response".
13. TERMINATION: If "compose_response" has already completed and we are finished, route to "__end__".

Return ONLY a JSON response in the following format:
{{
  "thought": "<one sentence reasoning explanation of why you chose this step>",
  "next_step": "<understand_intent|discover_schema|generate_sql|review_sql|execute_sql|generate_viz_config|analyze_insights|review_insights|compose_response|general_llm|disambiguate|insight_followup|__end__>"
}}"""

    # 100% accurate, sub-millisecond deterministic Prefrontal Cortex Routing Engine
    if state.get("pre_filter_response") or state.get("skip_pipeline"):
        next_step = "compose_response"
        thought_log = "Response is already pre-generated. Routing directly to response composer to finalize."
    elif not intent_type:
        next_step = "understand_intent"
        thought_log = "The Intent Type is currently empty, triggering initial classification."
    elif intent_type in ("greeting", "conversational", "off_topic"):
        next_step = "general_llm"
        thought_log = "Conversational or greeting intent detected, routing to general LLM responder."
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
        next_step = "generate_sql"
        thought_log = f"SQL execution failed with error: {error[:80]}. Routing back to SQL Gen for auto-fix."
    elif error:
        next_step = "compose_response"
        thought_log = "SQL execution failed and retries exhausted. Preparing error response."
    elif not query_results.get("rows"):
        # Query executed successfully but returned 0 rows! Route to compose_response directly
        next_step = "compose_response"
        thought_log = "Query returned 0 rows. Skipping analysis/visualization and routing to response composer."
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
    
    # Append the supervisor thought to the history log
    new_thoughts = thoughts + [f"Step decision: {next_step}. Reason: {thought_log}"]
    
    return {
        **state,
        "next_step": next_step,
        "supervisor_thoughts": new_thoughts
    }
