"""
LangGraph Agent Pipeline — Data Visualization Copilot
Orchestrates the 7-step pipeline:
  Intent → Schema → SQL → Execute → Analyze → Visualize → Respond

Each step is a pure async function that reads/writes to AnalyticsState.
LangGraph handles the DAG execution, state passing, and error propagation.
"""
from __future__ import annotations

import time
import structlog
from langgraph.graph import StateGraph, END

from backend.agent.state import AnalyticsState
from backend.agent.nodes.cache_check import check_qa_memory
from backend.agent.nodes.general_llm import handle_general_query
from backend.agent.nodes.intent import understand_intent
from backend.agent.nodes.schema import discover_schema
from backend.agent.nodes.sql_gen import generate_sql
from backend.agent.nodes.executor import execute_sql
from backend.agent.nodes.analyst import analyze_insights
from backend.agent.nodes.viz_config import generate_viz_config
from backend.agent.nodes.responder import compose_response
from backend.agent.nodes.insight_followup import handle_insight_followup, _is_insight_followup
from backend.agent.nodes.disambiguate import disambiguate
from backend.agent.nodes.route_tables import route_tables

log = structlog.get_logger(__name__)


def _after_cache_check(state: AnalyticsState) -> list[str]:
    """Route after cache check: if cached answer, skip to respond, else run parallel intent + routing."""
    if state.get("skip_pipeline") and state.get("pre_filter_response"):
        return ["compose_response"]
    return ["understand_intent", "route_tables"]


async def routing_gatekeeper(state: AnalyticsState) -> AnalyticsState:
    """A converging node that aggregates parallel intent classification and semantic table routing."""
    return state


def _after_gatekeeper(state: AnalyticsState) -> str:
    """Route after converging gatekeeper: dynamic branch based on intent and ambiguity."""
    if state.get("skip_pipeline"):
        return "skip_to_respond"

    intent_type = state.get("intent", {}).get("type", "")
    question = state.get("user_question", "")
    history = state.get("conversation_history", [])

    if _is_insight_followup(question, history):
        return "insight_followup"

    if intent_type in ("greeting", "conversational", "off_topic"):
        return "general_llm"

    if intent_type == "export_request":
        return "skip_to_respond"

    # Handle data queries based on ambiguity score
    if state.get("ambiguity_score", 0.0) > 0.6:
        return "disambiguate"

    return "discover_schema"


def _after_disambiguate(state: AnalyticsState) -> str:
    """Route: skip pipeline if disambiguation is needed."""
    if state.get("skip_pipeline"):
        return "skip_to_respond"
    return "discover_schema"


def _should_retry_sql(state: AnalyticsState) -> list[str]:
    """Route: retry SQL generation or proceed to analysis & visualization."""
    error = state.get("error")
    retry_count = state.get("sql_retry_count", 0)

    if error and retry_count < 2:  # allow up to 2 retry loops
        log.info("graph.retry_sql_attempt", retry_count=retry_count, error=error[:120])
        return ["retry"]
    
    return ["analyze", "viz"]


def build_graph() -> StateGraph:
    """Build and compile the LangGraph pipeline."""
    graph = StateGraph(AnalyticsState)

    # Register all nodes
    graph.add_node("check_qa_memory", check_qa_memory)
    graph.add_node("understand_intent", understand_intent)
    graph.add_node("disambiguate", disambiguate)
    graph.add_node("general_llm", handle_general_query)
    graph.add_node("route_tables", route_tables)
    graph.add_node("routing_gatekeeper", routing_gatekeeper)
    graph.add_node("discover_schema", discover_schema)
    graph.add_node("generate_sql", generate_sql)
    graph.add_node("execute_sql", execute_sql)
    graph.add_node("analyze_insights", analyze_insights)
    graph.add_node("generate_viz_config", generate_viz_config)
    graph.add_node("compose_response", compose_response)
    graph.add_node("insight_followup", handle_insight_followup)

    # Entry point: check QA memory first
    graph.set_entry_point("check_qa_memory")

    # Route after cache check: parallel split to understand_intent and route_tables!
    graph.add_conditional_edges(
        "check_qa_memory",
        _after_cache_check,
        {
            "compose_response": "compose_response",
            "understand_intent": "understand_intent",
            "route_tables": "route_tables",
        }
    )

    # Converge both parallel branches into routing_gatekeeper
    graph.add_edge("understand_intent", "routing_gatekeeper")
    graph.add_edge("route_tables", "routing_gatekeeper")

    # Dynamically branch after merging parallel runs
    graph.add_conditional_edges(
        "routing_gatekeeper",
        _after_gatekeeper,
        {
            "skip_to_respond": "compose_response",
            "general_llm": "general_llm",
            "insight_followup": "insight_followup",
            "disambiguate": "disambiguate",
            "discover_schema": "discover_schema",
        }
    )

    graph.add_conditional_edges(
        "disambiguate",
        _after_disambiguate,
        {
            "discover_schema": "discover_schema",
            "skip_to_respond": "compose_response",
        }
    )
    graph.add_edge("discover_schema", "generate_sql")
    graph.add_edge("generate_sql", "execute_sql")
    
    # Fan-out (Execute SQL loops back to generate_sql on failure, or runs Insights & Visualization in parallel)
    graph.add_conditional_edges(
        "execute_sql",
        _should_retry_sql,
        {
            "retry": "generate_sql",
            "analyze": "analyze_insights",
            "viz": "generate_viz_config",
        }
    )
    
    # Fan-in (Insights & Visualization join at Response Compose)
    graph.add_edge("analyze_insights", "compose_response")
    graph.add_edge("generate_viz_config", "compose_response")
    
    graph.add_edge("insight_followup", "compose_response")
    graph.add_edge("general_llm", "compose_response")
    graph.add_edge("compose_response", END)

    return graph.compile()


# Singleton compiled graph
_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


async def run_analytics_agent(
    question: str,
    datasource_id: str,
    session_id: str,
    conversation_id: str,
    conversation_history: list[dict],
    user_id: str = "anonymous",
) -> dict:
    """
    Main entry point for the analytics agent.
    Returns the final_response dict ready to send to the frontend.
    """
    t0 = time.perf_counter()

    from backend.agent.memory import vector_memory
    from backend.services.minio_conversation import minio_conversation_store

    # Load conversation history from MinIO if not provided
    if not conversation_history and conversation_id:
        minio_history = minio_conversation_store.get_conversation_history(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if minio_history:
            conversation_history = minio_history
            log.info("agent.loaded_minio_history", messages=len(minio_history))

    cached_sql = None
    if vector_memory.enabled:
        cached_payload = vector_memory.search_semantic_cache(question, user_id=user_id, threshold=0.92)
        if cached_payload and cached_payload.get("sql"):
            matched_q = cached_payload.get("question", "")
            
            # Semantic cache validation: ensure requested years and months match
            import re
            def _validate_match(q1: str, q2: str) -> bool:
                curr_years = set(re.findall(r'\b(20[12]\d)\b', q1))
                match_years = set(re.findall(r'\b(20[12]\d)\b', q2))
                if curr_years != match_years:
                    return False
                months = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december',
                          'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
                q1_lower, q2_lower = q1.lower(), q2.lower()
                q1_months = {m for m in months if re.search(r'\b' + m + r'\b', q1_lower)}
                q2_months = {m for m in months if re.search(r'\b' + m + r'\b', q2_lower)}
                if q1_months != q2_months:
                    return False
                q1_digit_months = set(re.findall(r'\b(0[1-9]|1[0-2])[-/](20[12]\d)\b', q1) + re.findall(r'\b(20[12]\d)[-/](0[1-9]|1[0-2])\b', q1))
                q2_digit_months = set(re.findall(r'\b(0[1-9]|1[0-2])[-/](20[12]\d)\b', q2) + re.findall(r'\b(20[12]\d)[-/](0[1-9]|1[0-2])\b', q2))
                if q1_digit_months != q2_digit_months:
                    return False
                
                # Reject matches if one question asks for a trend/chart and the other doesn't
                q1_is_trend = any(w in q1_lower for w in ["trend", "daily", "weekly", "chart", "graph", "plot", "map", "viz", "visualization"])
                q2_is_trend = any(w in q2_lower for w in ["trend", "daily", "weekly", "chart", "graph", "plot", "map", "viz", "visualization"])
                if q1_is_trend != q2_is_trend:
                    return False
                
                return True
                
            if _validate_match(question, matched_q):
                cached_sql = cached_payload["sql"]
                log.info("agent.semantic_cache_match", question=question, matched=matched_q)
            else:
                log.info("agent.semantic_cache_match_rejected_due_to_date_mismatch", question=question, matched=matched_q)

    initial_state: AnalyticsState = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "user_question": question,
        "datasource_id": datasource_id,
        "conversation_history": conversation_history,
        "user_id": user_id,
        "step_errors": [],
    }
    if cached_sql:
        initial_state["sql_query"] = cached_sql

    try:
        graph = get_graph()
        final_state = await graph.ainvoke(initial_state)

        total_ms = int((time.perf_counter() - t0) * 1000)
        log.info(
            "agent.complete",
            session_id=session_id,
            total_ms=total_ms,
            viz_type=final_state.get("viz_type"),
            row_count=final_state.get("query_results", {}).get("row_count", 0),
        )

        result = final_state.get("final_response", {})
        result["total_latency_ms"] = total_ms
        result["model_used"] = final_state.get("model_used", "")
        return result

    except Exception as exc:
        import traceback
        traceback.print_exc()
        total_ms = int((time.perf_counter() - t0) * 1000)
        log.error("agent.failed", error=str(exc), total_ms=total_ms)
        return {
            "text": f"I ran into an unexpected error. Please try again. ({str(exc)[:100]})",
            "chart": None,
            "insights": [],
            "key_metrics": {},
            "follow_up_questions": ["Try a simpler question", "Check your data source connection"],
            "sql": "",
            "row_count": 0,
            "viz_type": None,
            "total_latency_ms": total_ms,
            "error": str(exc),
        }
