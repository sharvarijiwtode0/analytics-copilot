"""Tests for supervisor.py — deterministic routing logic."""
import pytest
from unittest.mock import patch, AsyncMock
from backend.agent.nodes.supervisor import supervisor, _try_llm_routing


def _make_state(**overrides) -> dict:
    defaults = {
        "user_question": "show me sales",
        "intent": {},
        "schema_context": {},
        "sql_query": "",
        "sql_validated": False,
        "query_results": {},
        "insights": [],
        "viz_config": {},
        "error": None,
        "sql_retry_count": 0,
        "review_retry_count": 0,
        "insights_validated": False,
        "critic_retry_count": 0,
        "critic_feedback": "",
        "supervisor_thoughts": [],
        "skip_pipeline": False,
        "pre_filter_response": None,
        "ambiguity_score": 0.0,
        "locked_tables": None,
    }
    defaults.update(overrides)
    return defaults


@pytest.mark.asyncio
async def test_empty_intent_routes_to_understand_intent():
    state = _make_state(intent={})
    result = await supervisor(state)
    assert result["next_step"] == "understand_intent"


@pytest.mark.asyncio
async def test_greeting_routes_to_general_llm():
    state = _make_state(intent={"type": "greeting"})
    result = await supervisor(state)
    assert result["next_step"] == "general_llm"


@pytest.mark.asyncio
async def test_skip_pipeline_routes_to_compose():
    state = _make_state(skip_pipeline=True, pre_filter_response="Hi there!")
    result = await supervisor(state)
    assert result["next_step"] == "compose_response"


@pytest.mark.asyncio
async def test_no_schema_routes_to_discover():
    state = _make_state(intent={"type": "data_query"}, schema_context={})
    result = await supervisor(state)
    assert result["next_step"] == "discover_schema"


@pytest.mark.asyncio
async def test_no_sql_routes_to_generate():
    state = _make_state(
        intent={"type": "data_query"},
        schema_context={"relevant_tables": [{"name": "sales"}]},
    )
    result = await supervisor(state)
    assert result["next_step"] == "generate_sql"


@pytest.mark.asyncio
async def test_unvalidated_sql_routes_to_review():
    state = _make_state(
        intent={"type": "data_query"},
        schema_context={"relevant_tables": [{"name": "sales"}]},
        sql_query="SELECT * FROM sales",
        sql_validated=False,
        review_retry_count=0,
    )
    result = await supervisor(state)
    assert result["next_step"] == "review_sql"


@pytest.mark.asyncio
async def test_validated_no_results_routes_to_execute():
    state = _make_state(
        intent={"type": "data_query"},
        schema_context={"relevant_tables": [{"name": "sales"}]},
        sql_query="SELECT * FROM sales",
        sql_validated=True,
        query_results={},
    )
    result = await supervisor(state)
    assert result["next_step"] == "execute_sql"


@pytest.mark.asyncio
async def test_sql_error_with_retries_left_routes_to_generate():
    state = _make_state(
        intent={"type": "data_query"},
        schema_context={"relevant_tables": [{"name": "sales"}]},
        sql_query="SELECT * FROM sales",
        sql_validated=True,
        query_results={"columns": [], "rows": [], "row_count": 0},
        error="unknown_identifier: foo",
        sql_retry_count=0,
    )
    result = await supervisor(state)
    assert result["next_step"] == "generate_sql"


@pytest.mark.asyncio
async def test_sql_error_retries_exhausted_routes_to_compose():
    state = _make_state(
        intent={"type": "data_query"},
        schema_context={"relevant_tables": [{"name": "sales"}]},
        sql_query="SELECT * FROM sales",
        sql_validated=True,
        query_results={"columns": [], "rows": [], "row_count": 0},
        error="unknown_identifier: foo",
        sql_retry_count=2,
    )
    result = await supervisor(state)
    assert result["next_step"] == "compose_response"


@pytest.mark.asyncio
async def test_zero_rows_routes_to_compose():
    state = _make_state(
        intent={"type": "data_query"},
        schema_context={"relevant_tables": [{"name": "sales"}]},
        sql_query="SELECT * FROM sales",
        sql_validated=True,
        query_results={"columns": ["id"], "rows": [], "row_count": 0},
    )
    result = await supervisor(state)
    assert result["next_step"] == "compose_response"
    assert "zero_row_context" in result.get("query_results", {})


@pytest.mark.asyncio
async def test_thoughts_capped_at_20():
    state = _make_state(skip_pipeline=True, pre_filter_response="hi")
    state["supervisor_thoughts"] = [f"thought {i}" for i in range(20)]
    result = await supervisor(state)
    assert len(result["supervisor_thoughts"]) <= 20


@pytest.mark.asyncio
async def test_analytical_question_defaults_to_discover_on_llm_failure():
    state = _make_state(intent={"type": "analytical_question"})
    result = await supervisor(state)
    assert result["next_step"] == "discover_schema"


@pytest.mark.asyncio
async def test_analytical_question_uses_llm_suggestion():
    mock_resp = AsyncMock()
    mock_resp.content = "compose_response"
    with patch("backend.agent.llm.call_llm", new_callable=AsyncMock, return_value=mock_resp):
        state = _make_state(intent={"type": "analytical_question"})
        result = await supervisor(state)
    assert result["next_step"] == "compose_response"


@pytest.mark.asyncio
async def test_error_with_llm_suggestion():
    mock_resp = AsyncMock()
    mock_resp.content = "discover_schema"
    with patch("backend.agent.llm.call_llm", new_callable=AsyncMock, return_value=mock_resp):
        state = _make_state(
            intent={"type": "data_query"},
            schema_context={"relevant_tables": [{"name": "sales"}]},
            sql_query="SELECT bad_col FROM sales",
            sql_validated=True,
            query_results={"columns": [], "rows": [], "row_count": 0},
            error="unknown_identifier: bad_col",
            sql_retry_count=0,
        )
        result = await supervisor(state)
    assert result["next_step"] == "discover_schema"


@pytest.mark.asyncio
async def test_try_llm_routing_returns_none_on_invalid_response():
    mock_resp = AsyncMock()
    mock_resp.content = "not_a_real_step"
    with patch("backend.agent.llm.call_llm", new_callable=AsyncMock, return_value=mock_resp):
        step = await _try_llm_routing("test question", {})
    assert step is None


@pytest.mark.asyncio
async def test_try_llm_routing_returns_none_on_exception():
    with patch("backend.agent.llm.call_llm", new_callable=AsyncMock, side_effect=RuntimeError("fail")):
        step = await _try_llm_routing("test question", {})
    assert step is None
