"""
Node 1: Intent Understanding
Classifies what the user wants: chart, query, follow-up, comparison, trend, etc.
Fast model — runs in <500ms.

Role boundary: ONLY classifies intent and rephrases the question clearly.
Does NOT generate SQL. Does NOT access the database.
"""
from __future__ import annotations
import json
import structlog
from backend.agent.state import AnalyticsState
from backend.agent.llm import call_llm
from backend.agent.pre_filter import pre_classify

log = structlog.get_logger(__name__)


def _get_last_assistant_context(history: list[dict]) -> str:
    """Extract last assistant message content to provide context for follow-ups."""
    for msg in reversed(history):
        if msg.get("role") == "assistant":
            content = msg.get("content", "")
            return content[:300] if content else ""
    return ""


async def understand_intent(state: AnalyticsState) -> AnalyticsState:
    question = state["user_question"]

    # ─── STEP 1: Rule-based pre-filter (saves tokens, instant responses) ────────
    pre_result = pre_classify(question)

    # If pre-filter is confident, skip LLM entirely
    if pre_result["skip_llm"]:
        log.info("intent.pre_filter", type=pre_result["type"])
        return {
            "intent": {
                "type": pre_result["type"],
                "confidence": pre_result["confidence"],
            },
            "skip_pipeline": True,
            "pre_filter_response": pre_result.get("response"),
        }

    # ─── STEP 2: LLM-based intent classification for ambiguous queries ────────
    history = state.get("conversation_history", [])[-4:]  # last 2 turns for context

    history_text = "\n".join(
        f"{m['role'].upper()}: {m['content'][:250]}" for m in history
    ) if history else "None"

    prompt = f"""You are classifying a data analytics question for a business database.

Previous conversation:
{history_text}

Current question: "{question}"

Classify this question and return JSON only:
{{
  "type": "<chart_request|data_query|follow_up|analytical_question|insight_request|comparison|trend_analysis|export_request|greeting|conversational|off_topic|schema_info>",
  "chart_type_hint": "<bar|line|pie|scatter|heatmap|gauge|table|area|treemap|null>",
  "time_range": "<last_7_days|last_30_days|last_quarter|last_year|ytd|custom|null>",
  "aggregation": "<sum|count|avg|max|min|null>",
  "entities": ["<entity1>", "<entity2>"],
  "filters": {{"<field>": "<value>"}},
  "is_follow_up": <true|false>,
  "needs_comparison": <true|false>,
  "confidence": <0.0-1.0>,
  "rephrased_question": "<clearer standalone version of the question with full context>"
}}

Rules:
- greeting: Simple greetings like "hi", "hello", "gm", "good morning" — NO SQL needed, respond conversationally
- conversational: "who are you", "what can you do", "how are you", "what are you doing", "help", "tell me about yourself" — respond conversationally about the assistant's capabilities
- off_topic: Weather, general knowledge, time, news, sports, math, or anything clearly unrelated to the connected database — respond conversationally or answer concisely
- schema_info: user is asking about table columns, table definitions, database schema structure, listing tables, or describing/analyzing a specific table (e.g. "what columns are in table X", "describe table Y", "analyze columns in Z", "show tables")
- chart_request: user explicitly wants a chart/graph/visualization
- data_query: user wants to see/query data without specifying chart
- analytical_question: "why is X", "explain X", "what caused X", "how is X" — needs data + narrative explanation
- follow_up: references previous result ("now break that down by...", "make it a line chart", "how do these compare")
- insight_request: wants analysis ("why is X dropping?", "what's the trend?")
- comparison: comparing two things, periods, or categories
- trend_analysis: wants to see changes over time
- Chart Type Selection Rules:
  * For trend/time series (e.g. monthly values, daily trends): hint `line` or `area`.
  * For ranking/comparing distinct categories (e.g. by group, by category): hint `bar`.
  * For proportions/shares of a whole (e.g. share, contribution): hint `pie`.
  * For conversion/journey stages: hint `funnel`.
  * For multi-dimensional correlations: hint `heatmap`.
  * For a single KPI value or speed-style target: hint `gauge`.
- IMPORTANT: For follow_up questions, make "rephrased_question" a COMPLETE standalone question
  that includes what "these", "that", "it", "them" refers to based on the conversation history.
  Example: if last question was about Category A vs Category B and user asks "compare with last year",
  rephrase as "Compare Category A vs Category B in the current year vs last year"."""

    try:
        resp = await call_llm(
            messages=[{"role": "user", "content": prompt}],
            task="routing",
            max_tokens=350,
            temperature=0.0,
        )
        import re
        raw = resp.content.strip()
        json_match = re.search(r'(\{.*\})', raw, re.DOTALL)
        raw_json = json_match.group(1) if json_match else raw
        if "```" in raw_json:
            raw_json = raw_json.split("```")[1].replace("json", "").strip()
        intent = json.loads(raw_json)
    except Exception as exc:
        log.warning("intent.parse_failed", error=str(exc))
        intent = {
            "type": "data_query",
            "entities": [],
            "is_follow_up": False,
            "confidence": 0.5,
            "rephrased_question": question,
        }

    if not intent or not isinstance(intent, dict):
        intent = {
            "type": "data_query",
            "entities": [],
            "is_follow_up": False,
            "confidence": 0.5,
            "rephrased_question": question,
        }

    # For follow-up questions with vague pronouns, ensure we enriched the rephrased question.
    # If the LLM produced an identical rephrased question, enrich it ourselves.
    rephrased_q = intent.get("rephrased_question") or ""
    if intent.get("is_follow_up") and rephrased_q.strip() == question.strip():
        last_ctx = _get_last_assistant_context(history)
        if last_ctx:
            intent["rephrased_question"] = f"{question} (Context from previous answer: {last_ctx[:200]})"

    # Rule-based chart hint overrides based on explicit user keywords and business semantics
    q_lower = question.lower()
    if "line" in q_lower or "trend" in q_lower or "monthly sales" in q_lower:
        intent["chart_type_hint"] = "line"
    elif "bar" in q_lower or "column" in q_lower or "ranking" in q_lower or "compare" in q_lower:
        intent["chart_type_hint"] = "bar"
    elif "pie" in q_lower or "share" in q_lower or "percentage" in q_lower or "contribution" in q_lower:
        intent["chart_type_hint"] = "pie"
    elif "funnel" in q_lower or "conversion" in q_lower:
        intent["chart_type_hint"] = "funnel"
    elif "gauge" in q_lower or "meter" in q_lower:
        intent["chart_type_hint"] = "gauge"
    elif "heatmap" in q_lower or "heat map" in q_lower:
        intent["chart_type_hint"] = "heatmap"
    elif "scatter" in q_lower or "bubble" in q_lower:
        intent["chart_type_hint"] = "scatter"
    elif "table" in q_lower or "grid" in q_lower or "tabular" in q_lower:
        intent["chart_type_hint"] = "table"

    log.info("intent.classified", type=intent.get("type"), confidence=intent.get("confidence"),
             rephrased=(intent.get("rephrased_question") or "")[:80])
    return {"intent": intent}
