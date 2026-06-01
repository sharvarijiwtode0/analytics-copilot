"""
SSE Streaming Endpoint for Real-time Agent Progress
Streams actual node execution progress with partial results.
"""
from __future__ import annotations
import json
import asyncio
import uuid
from typing import AsyncGenerator
import structlog

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent.streaming_graph import get_streaming_graph
from backend.agent.state import AnalyticsState
from backend.database import AsyncSessionLocal
from backend.models.conversation import Conversation, Message

router = APIRouter()


class StreamingQueryRequest(BaseModel):
    question: str
    datasource_id: str = "default"
    conversation_id: str | None = None
    user_id: str = "anonymous"


async def _stream_agent_execution(
    question: str,
    datasource_id: str,
    conversation_id: str | None,
    user_id: str,
) -> AsyncGenerator[str, None]:
    """
    Execute agent and stream REAL progress updates via SSE.
    Yields JSON strings as each node completes with partial results.
    """
    # Emit start
    yield f"data: {json.dumps({'type': 'start', 'message': 'Starting analysis...'})}\n\n"

    # Get or create conversation
    conv_id = conversation_id
    conversation_history = []

    async with AsyncSessionLocal() as session:
        if conv_id:
            result = await session.execute(select(Conversation).where(Conversation.id == conv_id))
            conversation = result.scalar_one_or_none()
        else:
            conversation = None

        if not conversation:
            conv_id = str(uuid.uuid4())
            conversation = Conversation(
                id=conv_id,
                user_id=user_id,
                datasource_id=datasource_id,
                title=question[:100],
            )
            session.add(conversation)
            await session.flush()

        # Load history
        history_result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conv_id)
            .order_by(Message.created_at.desc())
            .limit(20)
        )
        history_messages = list(reversed(history_result.scalars().all()))
        conversation_history = [
            {"role": m.role, "content": m.content}
            for m in history_messages
        ]

        # Save user message
        user_msg = Message(
            id=str(uuid.uuid4()),
            conversation_id=conv_id,
            role="user",
            content=question,
        )
        session.add(user_msg)
        await session.commit()

    # ─── DISAMBIGUATION CHECK ─────────────────────────────────────────────────
    from backend.services.disambiguation import check_disambiguation
    disambiguation_result = await check_disambiguation(question)
    if disambiguation_result:
        result = {
            "conversation_id": conv_id,
            "message_id": str(uuid.uuid4()),
            "text": f"**Clarification Needed:** What do you mean by '{disambiguation_result['keyword']}'?",
            "chart": None,
            "insights": [f"Please select the intended meaning of '{disambiguation_result['keyword']}'"],
            "key_metrics": {},
            "follow_up_questions": disambiguation_result.get("meanings", disambiguation_result.get("options", [])),
            "sql": "",
            "row_count": 0,
            "viz_type": None,
            "columns": [],
            "rows": [],
            "total_latency_ms": 0,
            "model_used": "",
            "error": f"DISAMBIGUATION_NEEDED:{disambiguation_result['keyword']}:{','.join(disambiguation_result.get('meanings', disambiguation_result.get('options', [])))}",
        }
        # Save assistant message for disambiguation
        async with AsyncSessionLocal() as session:
            assistant_msg = Message(
                id=result["message_id"],
                conversation_id=conv_id,
                role="assistant",
                content=result["text"],
                sql_query="",
                query_results={"columns": [], "rows": [], "row_count": 0},
                viz_config=None,
                insights=result["insights"],
                follow_up_questions=result["follow_up_questions"],
                model_used="",
                latency_ms=0,
                error=result["error"],
            )
            session.add(assistant_msg)
            await session.commit()

        yield f"data: {json.dumps({'type': 'complete', 'result': result})}\n\n"
        return

    cached_sql = None
    cached_viz_type = None

    # ─── QA MEMORY CHECK ─────────────────────────────────────────────────────
    try:
        from backend.services.knowledge.business_knowledge import get_qa_memory_service
        qa_service = get_qa_memory_service()
        cached = qa_service.search(question, user_id=user_id, threshold=0.92)
        if cached and cached.get("question"):
            if cached.get("sql"):
                # Database question: save query and bypass early-return
                cached_sql = cached.get("sql")
                cached_viz_type = cached.get("viz_type")
            else:
                from backend.agent.utils import validate_cache_match
                if validate_cache_match(question, cached["question"]):
                    async with AsyncSessionLocal() as session:
                        # Save assistant message to database
                        assistant_msg = Message(
                            id=str(uuid.uuid4()),
                            conversation_id=conv_id,
                            role="assistant",
                            content=cached.get("answer", ""),
                            sql_query="",
                            query_results={
                                "columns": cached.get("columns", []),
                                "rows": [],
                                "row_count": 0,
                            },
                            viz_config=None,
                            insights=[],
                            follow_up_questions=[],
                            model_used="qa_memory_cache",
                            latency_ms=50,
                            error=None,
                        )
                        session.add(assistant_msg)
                        await session.commit()

                    result = {
                        "conversation_id": conv_id,
                        "message_id": assistant_msg.id,
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
                    }
                    yield f"data: {json.dumps({'type': 'complete', 'result': result})}\n\n"
                    return
                else:
                    structlog.get_logger(__name__).info("stream_cache_check.rejected_due_to_mismatch", question=question, matched=cached["question"])
    except Exception as e:
        structlog.get_logger(__name__).warning("stream_cache_check.failed", error=str(e))

    # ─── LLM CACHE CHECK ──────────────────────────────────────────────────────
    if not cached_sql:
        try:
            from backend.services.llm_cache import get_cache as _get_llm_cache
            cache = _get_llm_cache()
            cached_llm = await cache.get_async(question=question, datasource_id=datasource_id, user_id=user_id)
            if cached_llm:
                import time as _time
                age = _time.time() - cached_llm.get("cached_at", 0)
                if cached_llm.get("sql") and age > 300:
                    # Database question and older than 5 minutes -> Query Caching only (run live)
                    cached_sql = cached_llm.get("sql")
                    cached_viz_type = cached_llm.get("viz_type")
                else:
                    async with AsyncSessionLocal() as session:
                        # Save assistant message to database
                        assistant_msg = Message(
                            id=str(uuid.uuid4()),
                            conversation_id=conv_id,
                            role="assistant",
                            content=cached_llm.get("text", ""),
                            sql_query="",
                            query_results={
                                "columns": cached_llm.get("columns", []),
                                "rows": cached_llm.get("rows", [])[:50],
                                "row_count": cached_llm.get("row_count", 0),
                            },
                            viz_config=cached_llm.get("chart"),
                            insights=cached_llm.get("insights", []),
                            follow_up_questions=cached_llm.get("follow_up_questions", []),
                            model_used=cached_llm.get("model_used", "cache"),
                            latency_ms=0,
                            error=None,
                        )
                        session.add(assistant_msg)
                        await session.commit()

                    result = {
                        "conversation_id": conv_id,
                        "message_id": assistant_msg.id,
                        "text": cached_llm.get("text", ""),
                        "chart": cached_llm.get("chart"),
                        "insights": cached_llm.get("insights", []),
                        "key_metrics": cached_llm.get("key_metrics", {}),
                        "follow_up_questions": cached_llm.get("follow_up_questions", []),
                        "sql": "",
                        "sql_explanation": "",
                        "row_count": cached_llm.get("row_count", 0),
                        "viz_type": cached_llm.get("viz_type"),
                        "columns": cached_llm.get("columns", []),
                        "rows": [],
                        "total_latency_ms": 0,
                        "model_used": cached_llm.get("model_used", "cache"),
                    }
                    yield f"data: {json.dumps({'type': 'progress', 'step': 'cache_hit', 'progress': 100, 'message': 'Loaded from cache'})}\n\n"
                    yield f"data: {json.dumps({'type': 'complete', 'result': result})}\n\n"
                    return
        except Exception as e:
            structlog.get_logger(__name__).warning("stream_llm_cache_check.failed", error=str(e))

    # Run agent with REAL progress tracking
    try:
        graph_runner = get_streaming_graph()
        initial_state = AnalyticsState(
            session_id=conv_id,
            conversation_id=conv_id,
            user_question=question,
            datasource_id=datasource_id,
            conversation_history=conversation_history,
            user_id=user_id,
            step_errors=[],
        )
        if cached_sql:
            initial_state["sql_query"] = cached_sql
            initial_state["sql_validated"] = True
            initial_state["intent"] = {
                "type": "data_query",
                "confidence": 1.0,
                "rephrased_question": question,
                "chart_type_hint": cached_viz_type
            }
            from backend.services.db_intelligence import get_db_context
            try:
                db_ctx = get_db_context()
                tables = db_ctx.get("tables", {})
                matched_tables = []
                sql_lower = cached_sql.lower()
                for tname in tables.keys():
                    if tname.lower() in sql_lower:
                        matched_tables.append({"name": tname})
                initial_state["schema_context"] = {
                    "relevant_tables": matched_tables
                }
            except Exception:
                initial_state["schema_context"] = {
                    "relevant_tables": [{"name": "combined_sales_final"}]
                }

        # Stream real progress from graph execution
        async for update in graph_runner.astream(initial_state):
            yield f"data: {json.dumps(update)}\n\n"

            # If this is the final complete message, save to database
            if update.get("type") == "complete":
                result = update["result"]
                result["conversation_id"] = conv_id

                async with AsyncSessionLocal() as session:
                    # Save assistant message
                    assistant_msg = Message(
                        id=str(uuid.uuid4()),
                        conversation_id=conv_id,
                        role="assistant",
                        content=result.get("text", ""),
                        sql_query=result.get("sql", ""),
                        query_results={
                            "columns": result.get("columns", []),
                            "rows": result.get("rows", [])[:50],
                            "row_count": result.get("row_count", 0),
                        },
                        viz_config=result.get("chart"),
                        insights=result.get("insights", []),
                        follow_up_questions=result.get("follow_up_questions", []),
                        model_used=result.get("model_used", ""),
                        latency_ms=result.get("total_latency_ms", 0),
                        error=result.get("error"),
                    )
                    session.add(assistant_msg)
                    await session.commit()

                # Cache successful results (Canary-style 15-min cache)
                if not result.get("error") and result.get("row_count", 0) > 0:
                    try:
                        from backend.services.llm_cache import get_cache as _get_llm_cache
                        await _get_llm_cache().set_async(
                            question=question,
                            datasource_id=datasource_id,
                            user_id=user_id,
                            result={
                                "text": result.get("text", ""),
                                "chart": result.get("chart"),
                                "insights": result.get("insights", []),
                                "key_metrics": result.get("key_metrics", {}),
                                "follow_up_questions": result.get("follow_up_questions", []),
                                "sql": result.get("sql", ""),
                                "sql_explanation": result.get("sql_explanation", ""),
                                "row_count": result.get("row_count", 0),
                                "viz_type": result.get("viz_type"),
                                "columns": result.get("columns", []),
                                "rows": result.get("rows", []),
                                "total_latency_ms": result.get("total_latency_ms", 0),
                                "model_used": result.get("model_used", ""),
                            },
                        )
                    except Exception as e:
                        structlog.get_logger(__name__).warning("stream_llm_cache_set.failed", error=str(e))

    except Exception as exc:
        import traceback
        traceback.print_exc()
        yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"


@router.get("/stream")
async def stream_query(
    question: str,
    datasource_id: str = "default",
    conversation_id: str | None = None,
    user_id: str = "anonymous",
):
    """
    SSE streaming endpoint for REAL-TIME agent progress.

    Streams actual progress as each pipeline node completes:
    - Intent classification (what the user wants)
    - SQL generation (the query being built)
    - Query execution (row count as soon as available)
    - Insights (key metrics found)
    - Visualization (chart type)
    - Final response

    Usage in frontend:
    ```
    const eventSource = new EventSource('/api/v1/copilot/stream?question=Show+me+sales');
    eventSource.onmessage = (e) => {
        const data = JSON.parse(e.data);
        if (data.type === 'progress') {
            updateProgress(data.progress, data.message);
            if (data.data.sql) showSQL(data.data.sql);
            if (data.data.row_count !== undefined) showRowCount(data.data.row_count);
        }
        if (data.type === 'complete') displayResult(data.result);
        if (data.type === 'error') showError(data.error);
    };
    ```
    """
    return StreamingResponse(
        _stream_agent_execution(
            question=question,
            datasource_id=datasource_id,
            conversation_id=conversation_id,
            user_id=user_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.post("/stream")
async def stream_query_post(
    request: StreamingQueryRequest,
):
    """
    POST version of streaming endpoint for better request handling.

    Request body:
    ```json
    {
        "question": "Show me revenue by platform",
        "datasource_id": "default",
        "conversation_id": "optional-existing-id",
        "user_id": "user-123"
    }
    ```
    """
    return StreamingResponse(
        _stream_agent_execution(
            question=request.question,
            datasource_id=request.datasource_id,
            conversation_id=request.conversation_id,
            user_id=request.user_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
