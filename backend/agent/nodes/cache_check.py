"""Cache Check Node — Check QA memory for similar questions before running agent."""
import structlog
from backend.agent.state import AnalyticsState

log = structlog.get_logger(__name__)


async def check_qa_memory(state: AnalyticsState) -> AnalyticsState:
    """Check if this question has been asked before and return cached answer."""
    question = state.get("user_question", "")

    try:
        from backend.services.knowledge.business_knowledge import get_qa_memory_service
        qa_service = get_qa_memory_service()

        user_id = state.get("user_id", "anonymous")

        # First check: high similarity (≥0.92)
        cached = qa_service.search(question, user_id=user_id, threshold=0.92)
        if cached:
            # If it's a data query (contains SQL), DO NOT skip pipeline.
            # Reuse cached SQL but execute live for fresh data!
            if cached.get("sql"):
                log.info("cache_check.hit_high_similarity_query", question=question[:50], similarity="≥0.92")
                from backend.services.db_intelligence import get_db_context
                db_ctx = get_db_context()
                tables = db_ctx.get("tables", {})
                matched_tables = []
                sql_lower = cached.get("sql", "").lower()
                for tname in tables.keys():
                    if tname.lower() in sql_lower:
                        matched_tables.append({"name": tname})
                return {
                    **state,
                    "sql_query": cached.get("sql", ""),
                    "sql_validated": True,
                    "intent": {
                        "type": "data_query",
                        "confidence": 1.0,
                        "rephrased_question": question
                    },
                    "schema_context": {
                        "relevant_tables": matched_tables
                    }
                }
            
            # Normal conversational/greeting question: return cached answer directly
            log.info("cache_check.hit_high_similarity_conversational", question=question[:50], similarity="≥0.92")
            return {
                **state,
                "skip_pipeline": True,
                "pre_filter_response": {
                    "text": cached.get("answer", ""),
                    "chart": None,
                    "insights": [],
                    "key_metrics": {},
                    "follow_up_questions": [],
                    "sql": "",
                    "sql_explanation": "",
                    "row_count": 0,
                    "viz_type": cached.get("viz_type"),
                    "columns": cached.get("columns", []),
                    "rows": [],
                    "total_latency_ms": 50,
                    "model_used": "qa_memory_cache",
                    "from_cache": True,
                }
            }

        # Second check: medium similarity (0.75-0.92) = re-run SQL only
        cached_sql = qa_service.search(question, user_id=user_id, threshold=0.75)
        if cached_sql and cached_sql.get("sql"):
            log.info("cache_check.hit_medium_similarity", question=question[:50], similarity="0.75-0.92")
            from backend.services.db_intelligence import get_db_context
            db_ctx = get_db_context()
            tables = db_ctx.get("tables", {})
            matched_tables = []
            sql_lower = cached_sql.get("sql", "").lower()
            for tname in tables.keys():
                if tname.lower() in sql_lower:
                    matched_tables.append({"name": tname})
            return {
                **state,
                "sql_query": cached_sql.get("sql", ""),
                "sql_validated": True,
                "intent": {
                    "type": "data_query",
                    "confidence": 1.0,
                    "rephrased_question": question
                },
                "schema_context": {
                    "relevant_tables": matched_tables
                }
            }

        log.info("cache_check.miss", question=question[:50])
        return state

    except Exception as e:
        log.warning("cache_check.failed", error=str(e))
        return state
