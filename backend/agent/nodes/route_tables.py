"""
Node 2.1: Semantic Table Routing.
Parses catalog descriptions, boosts by user history, and checks for keyword ambiguity.
"""
from __future__ import annotations
import structlog
from backend.agent.state import AnalyticsState
from backend.services.table_catalog import score_tables
from backend.services.user_history import get_user_profile

log = structlog.get_logger(__name__)


async def route_tables(state: AnalyticsState) -> AnalyticsState:
    # Skip if locked_tables is already set (indicating clarification override)
    if state.get("locked_tables"):
        log.info("route_tables.skipped", reason="locked_tables_present", tables=state["locked_tables"])
        return state

    question = state.get("user_question", "")
    user_id = state.get("user_id", "anonymous")
    session_id = state.get("session_id")

    # 1. Fetch user history profile
    history_profile = get_user_profile(user_id=user_id, session_id=session_id)

    # 2. Score all 173 tables semantically
    ranked_tables = score_tables(question, user_id=user_id, history_profile=history_profile)

    # 3. Determine Ambiguity Score
    # We check if there are competing candidate tables from different business domains
    ambiguity_score = 0.0
    is_ambiguous = False
    
    if len(ranked_tables) > 1:
        top_table = ranked_tables[0]
        runner_up = ranked_tables[1]

        # Vague keyword checks
        vague_terms = ["sales", "revenue", "orders", "data", "report", "details", "performance"]
        q_words = set(question.lower().split())
        contains_vague_term = any(term in q_words for term in vague_terms)

        # If they belong to different domains, score highly, and have vague terms
        if top_table["domain"] != runner_up["domain"]:
            score_diff = top_table["score"] - runner_up["score"]
            # If the scores are very close, it is highly ambiguous
            if score_diff < 3.0:
                if contains_vague_term:
                    ambiguity_score = 0.8
                    is_ambiguous = True
                else:
                    ambiguity_score = 0.5

    if is_ambiguous:
        log.info("route_tables.ambiguous_detected", ambiguity_score=ambiguity_score,
                 candidates=[t["name"] for t in ranked_tables[:3]])
    else:
        # Clear intent: pre-populate relevant tables for discover_schema node
        top_picks = [t["name"] for t in ranked_tables[:4]]
        log.info("route_tables.clear_intent", selected=top_picks)

    return {
        "candidate_tables": ranked_tables,
        "ambiguity_score": ambiguity_score,
    }
