"""
Enhanced Agent Graph with Real-time Progress Tracking
Streams actual node execution progress instead of fake updates.
"""
from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator, Callable
from langgraph.graph import StateGraph
from langgraph.pregel import Pregel

from backend.agent.state import AnalyticsState
from backend.agent.nodes import (
    intent, schema, sql_gen, executor,
    analyst, viz_config, responder, general_llm, disambiguate
)
from backend.agent.nodes.route_tables import route_tables
from backend.agent.nodes.insight_followup import handle_insight_followup, _is_insight_followup
from backend.agent.nodes.general_llm import handle_general_query
from backend.agent.nodes.sql_reviewer import review_sql
from backend.agent.nodes.supervisor import supervisor
from backend.agent.nodes.critic import review_insights
import structlog

log = structlog.get_logger(__name__)

async def start_node(state: AnalyticsState) -> dict:
    """An entry point node to start the graph before fanning out."""
    return {"datasource_id": state.get("datasource_id")}


class StreamingGraphRunner:
    """Wraps the LangGraph execution with real progress streaming."""

    # Step definitions with descriptions and progress percentages
    STEPS = {
        "supervisor": {
            "progress": 5,
            "message": "Supervisor planning reasoning loop...",
            "description": "Orchestrating the cognitive agent workflow"
        },
        "understand_intent": {
            "progress": 10,
            "message": "Understanding your question...",
            "description": "Analyzing what you're asking for"
        },
        "route_tables": {
            "progress": 15,
            "message": "Selecting target tables...",
            "description": "Matching semantic table blueprints"
        },
        "disambiguate": {
            "progress": 20,
            "message": "Clarifying ambiguous terms...",
            "description": "Checking if any terms need clarification"
        },
        "general_llm": {
            "progress": 50,
            "message": "Generating response...",
            "description": "Formulating a conversational answer"
        },
        "discover_schema": {
            "progress": 25,
            "message": "Exploring database structure...",
            "description": "Finding relevant tables and columns"
        },
        "generate_sql": {
            "progress": 40,
            "message": "Writing SQL query...",
            "description": "Generating the database query"
        },
        "review_sql": {
            "progress": 50,
            "message": "DBA reviewing SQL query...",
            "description": "Validating query rules and database compatibility"
        },
        "execute_sql": {
            "progress": 65,
            "message": "Running query on database...",
            "description": "Fetching your data"
        },
        "analyze_insights": {
            "progress": 75,
            "message": "Analyzing results...",
            "description": "Finding patterns and insights"
        },
        "review_insights": {
            "progress": 82,
            "message": "Validating insights correctness...",
            "description": "Factual cross-referencing and reflection checks"
        },
        "generate_viz_config": {
            "progress": 88,
            "message": "Creating visualization...",
            "description": "Building your chart"
        },
        "compose_response": {
            "progress": 95,
            "message": "Preparing response...",
            "description": "Finalizing the answer"
        },
    }

    def __init__(self):
        self.graph = self._build_streaming_graph()
        self.progress_callback: Callable[[str, int, dict], Any] | None = None

    def set_progress_callback(self, callback: Callable[[str, int, dict], Any]):
        """Set callback for progress updates."""
        self.progress_callback = callback

    def _emit_progress(self, step: str, data: dict | None = None):
        """Emit progress update if callback is set."""
        if self.progress_callback:
            step_info = self.STEPS.get(step, {
                "progress": 50,
                "message": f"Processing {step}...",
                "description": ""
            })
            
            # Dynamic Paraphraser Integration!
            # Generate descriptive, highly warm analyst narratives locally.
            from backend.services.hf_loader import hf_loader
            tables = None
            if data and isinstance(data, dict):
                tables = data.get("tables")
            
            warm_message = hf_loader.generate_warm_narrative(
                step=step,
                tables=tables,
                domain="E-Commerce"
            )
            
            self.progress_callback(
                step,
                step_info["progress"],
                {
                    **step_info,
                    "message": warm_message, # Override with our beautifully dynamic human message
                    "data": data or {}
                }
            )

    def _wrap_node(self, original_func, node_name: str):
        """Wrap a node function to emit progress before/after execution."""
        async def wrapped(state: AnalyticsState) -> AnalyticsState:
            # Emit start
            self._emit_progress(node_name, {"status": "starting"})

            try:
                # Run original node
                result = await original_func(state)
                
                # Diagnostic logging to trace exactly what is returned
                try:
                    result_keys = list(result.keys()) if isinstance(result, dict) else None
                    log.info("streaming_graph.node_debug", node=node_name, keys=result_keys, type=str(type(result)))
                except Exception as dbg_err:
                    log.info("streaming_graph.node_debug_error", node=node_name, error=str(dbg_err))

                # Emit completion with partial results
                partial_data = {"status": "complete"}

                # Add node-specific data for streaming
                if node_name == "understand_intent":
                    intent = result.get("intent", {})
                    partial_data["intent"] = intent.get("type")
                    partial_data["rephrased_question"] = intent.get("rephrased_question")

                elif node_name == "discover_schema":
                    # Emit tables being used
                    sc = result.get("schema_context", {})
                    relevant_tables = sc.get("relevant_tables", [])
                    table_names = []
                    if isinstance(relevant_tables, list):
                        for t in relevant_tables:
                            if isinstance(t, dict) and "name" in t:
                                table_names.append(t["name"])
                            elif isinstance(t, str):
                                table_names.append(t)
                    partial_data["tables"] = table_names

                elif node_name == "generate_sql":
                    partial_data["sql"] = result.get("sql_query", "")

                elif node_name == "supervisor":
                    partial_data["next_step"] = result.get("next_step")
                    thoughts = result.get("supervisor_thoughts", [])
                    partial_data["thoughts"] = thoughts[-1] if thoughts else ""

                elif node_name == "review_sql":
                    partial_data["sql_validated"] = result.get("sql_validated")
                    partial_data["dba_feedback"] = result.get("dba_feedback", "")

                elif node_name == "execute_sql":
                    qr = result.get("query_results", {})
                    partial_data["row_count"] = qr.get("row_count", 0)
                    partial_data["columns"] = qr.get("columns", [])[:10]

                elif node_name == "analyze_insights":
                    partial_data["insights"] = result.get("insights", [])[:3]
                    partial_data["key_metrics"] = result.get("key_metrics", {})

                elif node_name == "review_insights":
                    partial_data["insights_validated"] = result.get("insights_validated")
                    partial_data["critic_feedback"] = result.get("critic_feedback", "")

                elif node_name == "generate_viz_config":
                    partial_data["viz_type"] = result.get("viz_type")

                self._emit_progress(node_name, partial_data)
                return result

            except Exception as e:
                self._emit_progress(node_name, {
                    "status": "error",
                    "error": str(e)
                })
                raise

        return wrapped

    def _build_streaming_graph(self) -> StateGraph:
        """Build the graph with progress-wrapped nodes."""
        graph = StateGraph(AnalyticsState)

        # Wrap nodes with progress tracking
        graph.add_node(
            "understand_intent",
            self._wrap_node(intent.understand_intent, "understand_intent")
        )
        graph.add_node(
            "disambiguate",
            self._wrap_node(disambiguate.disambiguate, "disambiguate")
        )
        graph.add_node(
            "general_llm",
            self._wrap_node(general_llm.handle_general_query, "general_llm")
        )
        graph.add_node(
            "discover_schema",
            self._wrap_node(schema.discover_schema, "discover_schema")
        )
        graph.add_node(
            "route_tables",
            self._wrap_node(route_tables, "route_tables")
        )
        graph.add_node(
            "supervisor",
            self._wrap_node(supervisor, "supervisor")
        )
        graph.add_node(
            "generate_sql",
            self._wrap_node(sql_gen.generate_sql, "generate_sql")
        )
        graph.add_node(
            "review_sql",
            self._wrap_node(review_sql, "review_sql")
        )
        graph.add_node(
            "execute_sql",
            self._wrap_node(executor.execute_sql, "execute_sql")
        )
        graph.add_node(
            "analyze_insights",
            self._wrap_node(analyst.analyze_insights, "analyze_insights")
        )
        graph.add_node(
            "generate_viz_config",
            self._wrap_node(viz_config.generate_viz_config, "generate_viz_config")
        )
        graph.add_node(
            "compose_response",
            self._wrap_node(responder.compose_response, "compose_response")
        )
        graph.add_node(
            "insight_followup",
            self._wrap_node(handle_insight_followup, "insight_followup")
        )
        graph.add_node(
            "review_insights",
            self._wrap_node(review_insights, "review_insights")
        )

        # Same routing logic as original graph

        # Single starter entry point node routing to supervisor
        graph.add_node("start_node", start_node)
        graph.set_entry_point("start_node")
        graph.add_edge("start_node", "supervisor")

        # All nodes transition directly back to supervisor
        graph.add_edge("understand_intent", "supervisor")
        graph.add_edge("discover_schema", "supervisor")
        graph.add_edge("generate_sql", "supervisor")
        graph.add_edge("review_sql", "supervisor")
        graph.add_edge("execute_sql", "supervisor")
        graph.add_edge("analyze_insights", "supervisor")
        graph.add_edge("generate_viz_config", "supervisor")
        graph.add_edge("general_llm", "supervisor")
        graph.add_edge("disambiguate", "supervisor")
        graph.add_edge("insight_followup", "supervisor")
        graph.add_edge("route_tables", "supervisor")
        graph.add_edge("review_insights", "supervisor")

        # The supervisor conditional routing edge
        from backend.agent.graph import _route_next
        graph.add_conditional_edges(
            "supervisor",
            _route_next,
            {
                "understand_intent": "understand_intent",
                "discover_schema": "discover_schema",
                "generate_sql": "generate_sql",
                "review_sql": "review_sql",
                "execute_sql": "execute_sql",
                "analyze_insights": "analyze_insights",
                "generate_viz_config": "generate_viz_config",
                "review_insights": "review_insights",
                "compose_response": "compose_response",
                "general_llm": "general_llm",
                "disambiguate": "disambiguate",
                "insight_followup": "insight_followup",
                "route_tables": "route_tables",
                "__end__": "__end__",
            }
        )

        graph.add_edge("compose_response", "__end__")

        return graph.compile()

    async def astream(
        self,
        initial_state: AnalyticsState,
    ) -> AsyncIterator[dict]:
        """
        Execute graph and yield progress updates.

        Yields dicts with:
        - type: "progress" | "complete" | "error"
        - step: current node name
        - progress: 0-100
        - message: user-friendly description
        - data: partial results from each node
        """
        t0 = time.perf_counter()

        # Compact history to keep context windows small and save tokens (ReMe pattern)
        if initial_state.get("conversation_history"):
            from backend.agent.utils import compact_history
            initial_state["conversation_history"] = await compact_history(initial_state["conversation_history"])

        # Generator to collect progress updates
        progress_queue = []

        def progress_callback(step: str, progress: int, info: dict):
            progress_queue.append({
                "type": "progress",
                "step": step,
                "progress": progress,
                "message": info["message"],
                "data": info.get("data", {}),
                "timestamp": time.time()
            })

        self.set_progress_callback(progress_callback)

        try:
            # Run the graph using astream so we can yield updates in real-time
            final_state = initial_state
            async for output in self.graph.astream(initial_state):
                # Drain and yield any progress events generated during this node's execution
                while progress_queue:
                    yield progress_queue.pop(0)
                
                # Keep track of the latest state
                for node_name, state in output.items():
                    final_state = state

            # Emit any remaining queued progress updates
            while progress_queue:
                yield progress_queue.pop(0)

            # Final result
            total_ms = int((time.perf_counter() - t0) * 1000)
            result = final_state.get("final_response", {})
            result["total_latency_ms"] = total_ms
            result["model_used"] = final_state.get("model_used", "")

            yield {
                "type": "complete",
                "result": result,
                "total_latency_ms": total_ms
            }

        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            # Emit queued progress before error
            for update in progress_queue:
                yield update

            yield {
                "type": "error",
                "error": str(exc),
                "traceback": tb,
                "step": progress_queue[-1]["step"] if progress_queue else "unknown"
            }


# Singleton
_streaming_graph = None


def get_streaming_graph() -> StreamingGraphRunner:
    global _streaming_graph
    if _streaming_graph is None:
        _streaming_graph = StreamingGraphRunner()
    return _streaming_graph
