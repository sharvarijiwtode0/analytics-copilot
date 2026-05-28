"""
Shared state object that flows through all 7 LangGraph nodes.
Each node reads from and writes to this TypedDict.
"""
from __future__ import annotations
from typing import TypedDict, Any


class AnalyticsState(TypedDict, total=False):
    # Input
    session_id: str
    conversation_id: str
    user_question: str
    datasource_id: str
    conversation_history: list[dict]   # [{role, content}]
    user_id: str

    # Step 1: Intent Understanding
    intent: dict
    # {
    #   "type": "chart_request" | "data_query" | "follow_up" | "insight_request" | "comparison" | "trend",
    #   "chart_type_hint": "bar" | "line" | "pie" | "scatter" | "heatmap" | None,
    #   "time_range": "last_quarter" | "2026" | None,
    #   "aggregation": "sum" | "count" | "avg" | None,
    #   "entities": ["sales", "region"],
    #   "is_follow_up": bool,
    #   "confidence": float
    # }

    # Step 2: Schema Discovery
    schema_context: dict
    # {
    #   "relevant_tables": [{"name": str, "columns": [...], "description": str}],
    #   "suggested_joins": [...],
    #   "sample_data": {...}
    # }

    # Step 3: SQL Generation
    sql_query: str
    sql_validated: bool
    sql_explanation: str

    # Step 4: Query Execution
    query_results: dict
    # {
    #   "columns": [...],
    #   "rows": [...],
    #   "row_count": int,
    #   "execution_time_ms": int
    # }

    # Step 5: Insight & Analysis
    insights: list[str]
    key_metrics: dict
    anomalies: list[str]

    # Step 6: Visualization Config
    viz_config: dict     # Full Apache ECharts option object
    viz_type: str        # bar | line | pie | scatter | heatmap | gauge | table

    # Step 7: Final Response
    response_text: str
    follow_up_questions: list[str]
    final_response: dict  # complete response sent to frontend

    # Routing & Pre-filter fields
    skip_pipeline: bool
    pre_filter_response: dict  # Response from rule-based pre-filter
    insight_followup_response: dict  # Response from insight follow-up handler

    # Metadata
    model_used: str
    total_tokens: int
    total_latency_ms: int
    error: str | None
    step_errors: list[str]
    sql_retry_count: int

    # Routing and Clarification
    candidate_tables: list[dict]
    ambiguity_score: float
    locked_tables: list[str]
