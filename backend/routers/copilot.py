"""
AI Copilot Chat Router — the main endpoint.
POST /api/v1/copilot/query     → send question, get chart + insights
POST /api/v1/copilot/upload    → upload CSV/Excel for analysis
GET  /api/v1/copilot/history   → conversation history
GET  /api/v1/copilot/schema    → datasource schema
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agent.graph import run_analytics_agent
from backend.auth.dependencies import get_current_user_optional
from backend.data.connector import upload_csv_as_datasource, get_schema, register_datasource
from backend.database import get_db, AsyncSessionLocal
from backend.models.conversation import Conversation, Message
from backend.models.user import User
from backend.services.llm_cache import get_cache as _get_llm_cache
from backend.services.query_analytics import log_query

router = APIRouter()
DbDep = Annotated[AsyncSession, Depends(get_db)]
UserDep = Annotated[User | None, Depends(get_current_user_optional)]


# ─── Request / Response models ─────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str
    datasource_id: str = "default"
    conversation_id: str | None = None


class QueryResponse(BaseModel):
    conversation_id: str
    message_id: str
    text: str
    chart: dict | None = None
    insights: list[str] = []
    key_metrics: dict = {}
    follow_up_questions: list[str] = []
    sql: str = ""
    sql_explanation: str = ""
    row_count: int = 0
    viz_type: str | None = None
    columns: list = []
    rows: list = []
    total_latency_ms: int = 0
    model_used: str = ""
    error: str | None = None


# ─── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/profile")
async def profile_endpoint():
    import time
    from backend.agent.state import AnalyticsState
    from backend.agent.nodes.intent import understand_intent
    from backend.agent.nodes.schema import discover_schema
    from backend.agent.nodes.sql_gen import generate_sql
    from backend.agent.nodes.executor import execute_sql
    from backend.agent.nodes.analyst import analyze_insights
    from backend.agent.nodes.viz_config import generate_viz_config
    from backend.agent.nodes.responder import compose_response

    state: AnalyticsState = {
        "session_id": "profile_session",
        "conversation_id": "profile_conv",
        "user_question": "What is the total number of items in the combined_sales_final table?",
        "datasource_id": "limese",
        "conversation_history": [],
        "user_id": "anonymous",
        "step_errors": [],
    }

    steps = [
        ("intent", understand_intent),
        ("schema", discover_schema),
        ("sql_gen", generate_sql),
        ("executor", execute_sql),
        ("analyst", analyze_insights),
        ("viz_config", generate_viz_config),
        ("responder", compose_response),
    ]

    results = []
    for name, node in steps:
        t0 = time.perf_counter()
        try:
            state = await node(state)
            duration = time.perf_counter() - t0
            results.append({
                "step": name,
                "duration_seconds": round(duration, 3),
                "success": True,
                "error": state.get("error") if "error" in state else None
            })
        except Exception as e:
            duration = time.perf_counter() - t0
            results.append({
                "step": name,
                "duration_seconds": round(duration, 3),
                "success": False,
                "error": str(e)
            })
            break

    from backend.config import settings
    from backend.agent.llm import call_llm
    
    t_start = time.perf_counter()
    test_resp = await call_llm(
        messages=[{"role": "user", "content": "Respond with the word 'Hello' only."}],
        task="routing"
    )
    test_duration = time.perf_counter() - t_start

    return {
        "fast_model": settings.llm_fast_model,
        "smart_model": settings.llm_smart_model,
        "test_llm": {
            "model_used": test_resp.model,
            "latency_seconds": round(test_duration, 3),
            "response": test_resp.content,
        },
        "profiling_results": results
    }

@router.get("/debug")
async def debug_endpoint():
    import sys
    import importlib
    import traceback
    
    # Force reload of streaming_graph to apply the absolute latest file changes
    if "backend.agent.streaming_graph" in sys.modules:
        importlib.reload(sys.modules["backend.agent.streaming_graph"])
        
    from backend.agent.streaming_graph import get_streaming_graph
    from backend.agent.state import AnalyticsState

    q = "Show overall catalog margins and average profit margins per product"
    with open("/home/ambarish/analytics-copilot/debug_output.txt", "w") as f:
        f.write("Starting graph execution diagnostic...\n")
        try:
            graph_runner = get_streaming_graph()
            initial_state = AnalyticsState(
                session_id="debug_session",
                conversation_id="debug_conv",
                user_question=q,
                datasource_id="limese",
                conversation_history=[],
                user_id="anonymous",
                step_errors=[],
            )
            f.write("Running graph_runner.graph.astream directly...\n")
            async for output in graph_runner.graph.astream(initial_state):
                f.write(f"Yielded: {output}\n")
            f.write("Finished without exceptions.\n")
            return {"status": "success", "message": "Written to debug_output.txt"}
        except Exception as exc:
            tb = traceback.format_exc()
            f.write(f"\n!!! EXCEPTION: {str(exc)}\n")
            f.write(tb)
            return {"status": "error", "error": str(exc), "traceback": tb}

@router.post("/query", response_model=QueryResponse)
async def query(payload: QueryRequest, current_user: UserDep = None) -> QueryResponse:
    """
    Main chat endpoint. Send a question, get AI-powered chart + insights.
    Includes LLM caching (15-min TTL) — repeat questions return instantly.

    Example:
      POST /api/v1/copilot/query
      {"question": "Show me sales by region last quarter", "datasource_id": "mydb"}
    """
    # ─── SECURITY: Block modification requests BEFORE cache check ─────────────
    question_upper = payload.question.upper()
    dangerous_keywords = ["UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "INSERT", "CREATE", "GRANT"]
    found_keyword = next((kw for kw in dangerous_keywords if kw in question_upper), None)
    if found_keyword:
        return QueryResponse(
            conversation_id=str(uuid.uuid4()),
            message_id=str(uuid.uuid4()),
            text=(
                "🔒 **You don't have permission to perform this action**\n\n"
                f"You asked me to **{found_keyword}** data, but I only have **read-only access**.\n\n"
                "I can:\n"
                "• ✅ Query and analyze data\n"
                "• ✅ Generate charts and visualizations\n"
                "• ✅ Provide insights and explanations\n\n"
                "I **cannot**:\n"
                "• ❌ Modify, update, or delete records\n"
                "• ❌ Create or drop tables\n"
                "• ❌ Change database structure\n\n"
                "Please use your database admin tools if you need to modify data. "
                "I'm here to help you analyze and explore your data!"
            ),
            chart=None,
            insights=["You attempted a data modification operation", "The system has read-only access enabled"],
            key_metrics={},
            follow_up_questions=[
                "Show me the current data",
                "What tables are available?",
                "Help me analyze the data"
            ],
            sql="",
            row_count=0,
            viz_type=None,
            columns=[],
            rows=[],
            total_latency_ms=0,
            model_used="",
            error=f"Read-only access: {found_keyword} operation not permitted",
        )

    # ─── DISAMBIGUATION CHECK ─────────────────────────────────────────────────
    # Check if question contains ambiguous keywords that need clarification
    from backend.services.disambiguation import check_disambiguation
    disambiguation_result = await check_disambiguation(payload.question)
    if disambiguation_result:
        # Return special response indicating disambiguation is needed
        return QueryResponse(
            conversation_id=str(uuid.uuid4()),
            message_id=str(uuid.uuid4()),
            text=f"**Clarification Needed:** What do you mean by '{disambiguation_result['keyword']}'?",
            chart=None,
            insights=[f"Please select the intended meaning of '{disambiguation_result['keyword']}'"],
            key_metrics={},
            follow_up_questions=disambiguation_result.get("meanings", []),
            sql="",
            row_count=0,
            viz_type=None,
            columns=[],
            rows=[],
            total_latency_ms=0,
            model_used="",
            error=f"DISAMBIGUATION_NEEDED:{disambiguation_result['keyword']}:{','.join(disambiguation_result.get('meanings', []))}",
        )

    # Get user_id from authenticated user or fall back to anonymous
    user_id = current_user.id if current_user else "anonymous"

    # Check LLM cache first (Canary pattern — 80% latency reduction on repeats)
    cache = _get_llm_cache()
    cached = await cache.get_async(question=payload.question, datasource_id=payload.datasource_id, user_id=user_id)
    if cached:
        # Get or create conversation
        conversation_id = payload.conversation_id
        conversation = None

        async with AsyncSessionLocal() as session:
            if conversation_id:
                result = await session.execute(select(Conversation).where(Conversation.id == conversation_id))
                conversation = result.scalar_one_or_none()

            if not conversation:
                conversation = Conversation(
                    id=conversation_id or str(uuid.uuid4()),
                    user_id=user_id,
                    datasource_id=payload.datasource_id,
                    title=payload.question[:100],
                )
                session.add(conversation)
                await session.flush()
                conversation_id = conversation.id

            # Save user message
            user_msg = Message(
                id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                role="user",
                content=payload.question,
            )
            session.add(user_msg)

            # Save cached assistant message to database
            cache_fields = {k: v for k, v in cached.items() if k in QueryResponse.model_fields}
            assistant_msg = Message(
                id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                role="assistant",
                content=cache_fields.get("text", ""),
                sql_query=cache_fields.get("sql", ""),
                query_results={
                    "columns": cache_fields.get("columns", []),
                    "rows": cache_fields.get("rows", [])[:50],
                    "row_count": cache_fields.get("row_count", 0),
                },
                viz_config=cache_fields.get("chart"),
                insights=cache_fields.get("insights", []),
                follow_up_questions=cache_fields.get("follow_up_questions", []),
                model_used=cache_fields.get("model_used", "cache"),
                latency_ms=0,
                error=None,
            )
            session.add(assistant_msg)
            await session.commit()

            # Log the cached query for analytics
            try:
                await log_query(
                    db=session,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    datasource_id=payload.datasource_id,
                    question=payload.question,
                    intent_type="cached",
                    sql_query=cache_fields.get("sql", ""),
                    row_count=cache_fields.get("row_count", 0),
                    viz_type=cache_fields.get("viz_type"),
                    latency_ms=cache_fields.get("total_latency_ms", 0),
                    cache_hit=True,
                    model_used=cache_fields.get("model_used", "cache")
                )
            except Exception:
                pass

        # Role-based filtering for cached responses
        can_view_sql = current_user.can_view_sql() if current_user else True

        return QueryResponse(
            conversation_id=conversation_id,
            message_id=assistant_msg.id,
            **{k: v for k, v in cache_fields.items() if k not in ("sql", "sql_explanation") or can_view_sql},
        )

    # Get or create conversation and load history
    conversation_id = payload.conversation_id
    conversation = None
    conversation_history = []

    async with AsyncSessionLocal() as session:
        if conversation_id:
            result = await session.execute(select(Conversation).where(Conversation.id == conversation_id))
            conversation = result.scalar_one_or_none()

        if not conversation:
            conversation = Conversation(
                id=str(uuid.uuid4()),
                user_id=user_id,
                datasource_id=payload.datasource_id,
                title=payload.question[:100],
            )
            session.add(conversation)
            await session.flush()
            conversation_id = conversation.id

        # Load conversation history (last N messages)
        history_result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
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
            conversation_id=conversation_id,
            role="user",
            content=payload.question,
        )
        session.add(user_msg)
        await session.commit()

    # Run the 7-step agent pipeline
    try:
        agent_result = await run_analytics_agent(
            question=payload.question,
            datasource_id=payload.datasource_id,
            session_id=conversation_id,
            conversation_id=conversation_id,
            conversation_history=conversation_history,
            user_id=user_id,
        )
    except PermissionError as exc:
        # Read-only access violation - user-friendly error
        agent_result = {
            "error": str(exc),
            "text": (
                "🔒 **Read-Only Access**\n\n"
                "I can only query and analyze data — I cannot modify, delete, or create records. "
                "This is a security restriction to protect your data.\n\n"
                "If you need to modify data, please use your database admin tools directly. "
                "I'm here to help you explore and analyze your data!"
            ),
            "insights": [],
            "key_metrics": {},
            "follow_up_questions": [],
            "sql": "",
            "chart": None,
            "viz_type": None,
            "row_count": 0,
            "columns": [],
            "rows": [],
            "total_latency_ms": 0,
            "model_used": "",
        }

    async with AsyncSessionLocal() as session:
        # Log query for analytics (async, don't fail request on logging errors)
        try:
            intent_type = agent_result.get("intent", {}).get("type", "unknown") if isinstance(agent_result.get("intent"), dict) else "unknown"
            await log_query(
                db=session,
                user_id=user_id,
                conversation_id=conversation_id,
                datasource_id=payload.datasource_id,
                question=payload.question,
                intent_type=intent_type,
                sql_query=agent_result.get("sql", ""),
                row_count=agent_result.get("row_count", 0),
                viz_type=agent_result.get("viz_type"),
                latency_ms=agent_result.get("total_latency_ms", 0),
                cache_hit=False,
                model_used=agent_result.get("model_used", ""),
                error=agent_result.get("error")
            )
        except Exception:
            pass

        # Save assistant message
        assistant_msg = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            role="assistant",
            content=agent_result.get("text", ""),
            sql_query=agent_result.get("sql"),
            query_results={
                "columns": agent_result.get("columns", []),
                "rows": agent_result.get("rows", [])[:50],
                "row_count": agent_result.get("row_count", 0),
            },
            viz_config=agent_result.get("chart"),
            insights=agent_result.get("insights", []),
            follow_up_questions=agent_result.get("follow_up_questions", []),
            model_used=agent_result.get("model_used", ""),
            latency_ms=agent_result.get("total_latency_ms", 0),
            error=agent_result.get("error"),
        )
        session.add(assistant_msg)
        await session.commit()

    # Cache successful results (Canary-style 15-min cache)
    if not agent_result.get("error") and agent_result.get("row_count", 0) > 0:
        await cache.set_async(   # reuse cache object from top of function
            question=payload.question,
            datasource_id=payload.datasource_id,
            user_id=user_id,
            result={
                "text": agent_result.get("text", ""),
                "chart": agent_result.get("chart"),
                "insights": agent_result.get("insights", []),
                "key_metrics": agent_result.get("key_metrics", {}),
                "follow_up_questions": agent_result.get("follow_up_questions", []),
                "sql": agent_result.get("sql", ""),
                "sql_explanation": agent_result.get("sql_explanation", ""),
                "row_count": agent_result.get("row_count", 0),
                "viz_type": agent_result.get("viz_type"),
                "columns": agent_result.get("columns", []),
                "rows": agent_result.get("rows", []),
                "total_latency_ms": agent_result.get("total_latency_ms", 0),
                "model_used": agent_result.get("model_used", ""),
            },
        )

    # Role-based filtering: hide SQL for non-business_analyst users
    can_view_sql = current_user.can_view_sql() if current_user else True  # Anonymous users can see SQL for now
    can_export = current_user.can_export_data() if current_user else True

    return QueryResponse(
        conversation_id=conversation_id,
        message_id=assistant_msg.id,
        text=agent_result.get("text", ""),
        chart=agent_result.get("chart"),
        insights=agent_result.get("insights", []),
        key_metrics=agent_result.get("key_metrics", {}),
        follow_up_questions=agent_result.get("follow_up_questions", []),
        sql=agent_result.get("sql", "") if can_view_sql else "",
        sql_explanation=agent_result.get("sql_explanation", "") if can_view_sql else "",
        row_count=agent_result.get("row_count", 0),
        viz_type=agent_result.get("viz_type"),
        columns=agent_result.get("columns", []),
        rows=agent_result.get("rows", []),
        total_latency_ms=agent_result.get("total_latency_ms", 0),
        model_used=agent_result.get("model_used", ""),
        error=agent_result.get("error"),
    )


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    datasource_name: str = Form("uploaded_data"),
    current_user: UserDep = None,
) -> dict:
    """Upload a CSV or Excel file for analysis."""
    allowed_types = {"text/csv", "application/vnd.ms-excel",
                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    if file.content_type not in allowed_types and not (
        file.filename or "").endswith((".csv", ".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Only CSV and Excel files supported")

    ds_id = str(uuid.uuid4())
    file_bytes = await file.read()

    result = await upload_csv_as_datasource(file_bytes, file.filename or "upload.csv", ds_id)

    return {
        "datasource_id": ds_id,
        "name": datasource_name,
        "filename": file.filename,
        "schema": result.get("schema", {}),
        "message": f"File uploaded. You can now ask questions about '{datasource_name}'",
    }


@router.get("/schema/{datasource_id}")
async def get_datasource_schema(datasource_id: str) -> dict:
    """Get the schema (tables + columns) for a datasource."""
    schema = await get_schema(datasource_id)
    return schema


@router.get("/datasources")
async def list_datasources() -> list[dict]:
    """List all registered datasources."""
    from backend.data.connector import get_registered_datasources
    return get_registered_datasources()


@router.get("/history/{conversation_id}")
async def get_history(conversation_id: str, db: DbDep) -> dict:
    """Get conversation history."""
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    messages = result.scalars().all()
    return {
        "conversation_id": conversation_id,
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "chart": m.viz_config,
                "insights": m.insights or [],
                "sql": m.sql_query,
                "row_count": (m.query_results or {}).get("row_count", 0),
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
    }


@router.post("/datasource")
async def register_ds(payload: dict) -> dict:
    """Register a new database datasource."""
    ds_id = payload.get("id") or str(uuid.uuid4())
    register_datasource(ds_id, payload["type"], payload.get("config", {}))
    schema = await get_schema(ds_id)
    return {"datasource_id": ds_id, "schema": schema, "status": "registered"}


@router.post("/disambiguate")
async def disambiguate_question(
    question: str,
    selected_meaning: str,
    keyword: str,
    current_user: UserDep = None
) -> QueryResponse:
    """
    Re-run a question after user has selected the meaning of an ambiguous keyword.

    Example:
        User asks: "What is our ROI?"
        System returns disambiguation options for ROI
        User selects: "Return on Investment"
        Frontend calls this endpoint with the selected meaning
    """
    from backend.services.disambiguation import resolve_disambiguation

    # Inject the selected meaning into the question
    resolved_question = resolve_disambiguation(question, keyword, selected_meaning)

    # Run the query with the resolved question
    user_id = current_user.id if current_user else "anonymous"

    # Create a simple payload for the query function
    class SimplePayload:
        def __init__(self, q: str, ds: str, conv_id: str | None = None):
            self.question = q
            self.datasource_id = ds
            self.conversation_id = conv_id

    payload = SimplePayload(resolved_question, "default", None)

    return await query(payload, current_user)

