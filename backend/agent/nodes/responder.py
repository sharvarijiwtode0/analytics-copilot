"""
Node 7: Response & Follow-ups
Composes the final response: text + chart + insights + suggested next questions.
"""
from __future__ import annotations
import json
import structlog
from backend.agent.state import AnalyticsState
from backend.agent.memory import vector_memory
from backend.agent.llm import call_llm
from backend.services.minio_conversation import minio_conversation_store

log = structlog.get_logger(__name__)


async def _compose_comparison_response(state: AnalyticsState) -> dict:
    """
    Compose a response that merges web search results with internal analytics.
    Used for comparison questions (e.g., "How does our revenue compare to industry?")
    """
    question = state["user_question"]
    web_results = state.get("web_search_results", {})
    analytics_results = state.get("analytics_results", {})

    web_summary = web_results.get("summary", "No web search results available.")
    web_sources = web_results.get("sources", [])

    # Extract analytics data
    analytics_text = analytics_results.get("text", "")
    analytics_chart = analytics_results.get("chart")
    analytics_insights = analytics_results.get("insights", [])
    analytics_metrics = analytics_results.get("key_metrics", {})
    analytics_rows = analytics_results.get("row_count", 0)

    # Build a merged response
    response_text = f"""## 🌍 External Market Context

{web_summary}

"""

    if web_sources:
        response_text += "**Sources:**\n"
        for i, source in enumerate(web_sources[:3], 1):
            response_text += f"{i}. [{source['title']}]({source['url']})\n"

    response_text += """

---

## 📊 Your Internal Data

"""

    if analytics_text:
        # Add the analytics summary
        response_text += f"\n{analytics_text}"
    else:
        response_text += "Internal data query completed with results shown in the chart."

    if analytics_metrics:
        response_text += "\n\n**Key Internal Metrics:**\n"
        for key, value in list(analytics_metrics.items())[:5]:
            response_text += f"• **{key}**: {value}\n"

    # Generate follow-up questions for comparison
    follow_ups = [
        "Drill down into specific segments",
        "Compare with previous period",
        "What's driving the difference?"
    ]

    # Also include analytics follow-ups if available
    analytics_follow_ups = analytics_results.get("follow_up_questions", [])
    if analytics_follow_ups:
        follow_ups.extend(analytics_follow_ups[:2])

    log.info(
        "responder.comparison_complete",
        web_sources=len(web_sources),
        analytics_rows=analytics_rows
    )

    return {
        **state,
        "response_text": response_text,
        "follow_up_questions": follow_ups[:4],
        "final_response": {
            "text": response_text,
            "chart": analytics_chart,  # Show the internal analytics chart
            "insights": analytics_insights[:3],
            "key_metrics": analytics_metrics,
            "follow_up_questions": follow_ups[:4],
            "sql": analytics_results.get("sql", ""),
            "sql_explanation": analytics_results.get("sql_explanation", ""),
            "row_count": analytics_rows,
            "viz_type": analytics_results.get("viz_type"),
            "columns": analytics_results.get("columns", []),
            "rows": analytics_results.get("rows", [])[:200],
            "total_latency_ms": analytics_results.get("total_latency_ms", 0),
            "model_used": analytics_results.get("model_used", ""),
        }
    }


def _extract_tables_from_sql(sql: str) -> list[str]:
    """Extract table names from SQL query."""
    import re
    # Match FROM and JOIN clauses
    pattern = r'(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)'
    tables = re.findall(pattern, sql, re.IGNORECASE)
    return list(set(tables))


async def _get_conversational_response(intent_type: str, question: str, history: list[dict] = None) -> dict:
    """Generate conversational response dynamically using LLM for greetings, general queries and off-topic questions."""
    history_context = ""
    if history:
        recent_history = history[-5:]
        history_context = "\n".join([
            f"{msg.get('role', 'user')}: {msg.get('content', '')}"
            for msg in recent_history
        ])
        history_context = f"\n\nRecent conversation:\n{history_context}"

    if intent_type == "greeting":
        prompt = f"""You are a friendly Data Analytics Copilot for Limese (an e-commerce cosmetics brand).
User just greeted you with: "{question}"{history_context}

Respond naturally and warmly (1-2 sentences max). Then suggest 3 specific analytics questions they could ask about their business data.
Keep it concise and conversational. DO NOT use markdown headers like ## or ###."""
    elif intent_type == "conversational":
        prompt = f"""You are a Data Analytics Copilot for Limese (an e-commerce cosmetics brand).
User asked: "{question}"{history_context}

Explain what you can do naturally in 2-3 sentences. Focus on:
- Answering questions about sales, revenue, products, customers
- Generating charts and visualizations
- Analyzing trends and providing insights
- Working with data from multiple platforms (Nykaa, Myntra, Amazon, etc.)

End with 3 example questions they could ask. Be conversational, NOT robotic."""
    else:
        # off_topic
        prompt = f"""You are a helpful AI assistant for Limese Copilot.
User asked: "{question}"{history_context}

If this is a general knowledge question (math, facts, etc.), answer it directly and concisely.
If this is unrelated to business analytics, politely redirect them to ask about their data:
- Sales and revenue trends
- Product performance
- Platform comparisons
- Customer insights

Keep your response to 2-3 sentences max. Be conversational and helpful."""

    try:
        resp = await call_llm(
            messages=[{"role": "user", "content": prompt}],
            task="general",
            max_tokens=300,
            temperature=0.8,
        )
        text = resp.content.strip()
    except Exception as e:
        log.warning("responder.conversational_llm_failed", error=str(e))
        text = (
            f"Hello! I'm your Data Analytics Copilot. I can help you analyze sales, products, and trends. "
            f"How can I assist you with your data today?"
        )

    # Simple follow-up suggestions
    follow_up_questions = [
        "Show me revenue by platform",
        "What are the top selling products?",
        "Show monthly sales trend"
    ]

    return {
        "text": text,
        "chart": None,
        "insights": [],
        "follow_up_questions": follow_up_questions,
        "viz_type": None,
        "row_count": 0,
        "columns": [],
        "rows": [],
    }


async def compose_response(state: AnalyticsState) -> AnalyticsState:
    question = state["user_question"]
    insights = state.get("insights", [])
    key_metrics = state.get("key_metrics", {})
    anomalies = state.get("anomalies", [])
    viz_type = state.get("viz_type", "table")
    query_results = state.get("query_results", {})
    sql_explanation = state.get("sql_explanation", "")
    error = state.get("error")
    intent_type = state.get("intent", {}).get("type", "")
    intent_confidence = state.get("intent", {}).get("confidence", 0.0)

    # Check if pre-filter already handled this (greeting, off-topic)
    if state.get("pre_filter_response"):
        log.info("responder.pre_filter")
        return {
            **state,
            "response_text": state["pre_filter_response"]["text"],
            "follow_up_questions": state["pre_filter_response"].get("follow_up_questions", []),
            "final_response": state["pre_filter_response"],
        }

    # Check if LLM classified as greeting/off-topic but pre-filter didn't catch it
    # This handles cases like "gm", "what are you doing" that go through LLM
    if intent_type in ("greeting", "conversational", "off_topic"):
        log.info("responder.llm_classified_conversational", type=intent_type)
        greeting_response = await _get_conversational_response(intent_type, question, state.get("conversation_history"))
        return {
            **state,
            "response_text": greeting_response["text"],
            "follow_up_questions": greeting_response.get("follow_up_questions", []),
            "final_response": greeting_response,
        }

    # Check if this is a comparison query (web search + analytics merged)
    if state.get("is_comparison_query"):
        log.info("responder.comparison_query")
        return await _compose_comparison_response(state)

    # Check if this is an insight follow-up response
    if state.get("insight_followup_response"):
        log.info("responder.insight_followup")
        return {
            **state,
            "response_text": state["insight_followup_response"]["text"],
            "follow_up_questions": state["insight_followup_response"].get("follow_up_questions", []),
            "final_response": state["insight_followup_response"],
        }

    # Check if this is a schema information or catalog request
    if intent_type == "schema_info":
        log.info("responder.schema_info")
        schema_response = await _compose_schema_info_response(question)
        return {
            **state,
            "response_text": schema_response["text"],
            "follow_up_questions": schema_response.get("follow_up_questions", []),
            "final_response": schema_response,
        }

    # Check if this is an analytical question - generate narrative response
    intent_type = state.get("intent", {}).get("type", "")
    if intent_type == "analytical_question":
        # Even if no data, provide a helpful conversational response
        if query_results.get("row_count", 0) == 0:
            return await _compose_analytical_no_data_response(state)
        return await _compose_analytical_response(state)

    row_count = query_results.get("row_count", 0)

    # Handle error case
    if error and not row_count:
        return {
            **state,
            "response_text": f"I encountered an issue: {error}\n\nPlease try rephrasing your question or check the data source connection.",
            "follow_up_questions": [
                "Show me what tables are available",
                "Can you simplify the query?",
                "Show me a sample of the data",
            ],
            "final_response": {
                "text": f"I encountered an issue: {error}",
                "chart": None,
                "insights": [],
                "key_metrics": {},
                "follow_up_questions": [],
                "sql": state.get("sql_query", ""),
                "row_count": 0,
                "viz_type": None,
            },
        }

    # Build beautiful executive conversational narrative summary and generate follow-up questions in parallel to save 25 seconds
    import asyncio
    
    summary_task = None
    follow_ups_task = None
    
    if not error and row_count > 0:
        summary_prompt = f"""You are Limese's Senior Analytics Copilot. Write a highly professional, conversational executive summary answering the user's question based on the actual metrics and findings.
 
User Question: "{question}"
Key Metrics: {key_metrics}
Raw Bullet Point Insights: {insights}
Total Rows Returned: {row_count}
 
RULES:
1. Write a direct, polished narrative summary in 1-2 paragraphs max (3-5 sentences total).
2. DO NOT write lists, bullet points, or raw tables — the UI displays bullet points separately under "Key Insights".
3. Bold key metrics (e.g. **₹16.21 Cr** or **1.4L units**).
4. Use ₹ (Rupee) for Indian currency format. Never use USD or $ unless specifically asked.
5. Keep the tone conversational, helpful, and highly strategic for a business owner."""
        
        async def _run_summary_llm():
            try:
                resp = await call_llm(
                    messages=[{"role": "user", "content": summary_prompt}],
                    task="general",
                    max_tokens=350,
                    temperature=0.3,
                )
                return resp.content.strip()
            except Exception as exc:
                log.warning("responder.summary_llm_failed", error=str(exc))
                return ""
        
        summary_task = _run_summary_llm()

    sql = state.get("sql_query", "")
    columns = query_results.get("columns", [])
    
    async def _run_follow_ups_llm():
        try:
            return await _generate_follow_ups(question, insights, viz_type, sql, columns)
        except Exception:
            return _default_follow_ups(question)

    follow_ups_task = _run_follow_ups_llm()

    # Execute both LLM calls in parallel to eliminate sequential latency
    gathered_results = await asyncio.gather(
        summary_task if summary_task else asyncio.sleep(0, result=""),
        follow_ups_task
    )
    
    response_text = gathered_results[0]
    follow_ups = gathered_results[1]

    # Fallback to narrative paragraph if LLM fails or returned empty
    if not response_text and not error:
        if row_count == 0:
            response_text = "No data found for your query. Try adjusting the filters or time range."
        else:
            metrics_part = ""
            if key_metrics:
                metrics_part = " showing " + ", ".join(f"**{k}** of {v}" for k, v in list(key_metrics.items())[:3])
            
            response_text = f"I've analyzed the data for **{question}** and retrieved **{row_count}** matching records{metrics_part}. "
            if insights:
                # Remove leading bullet points/dashes if present
                clean_ins0 = insights[0].lstrip("*-• ").strip()
                response_text += f"A key takeaway from the analysis is: {clean_ins0}. "
                if len(insights) > 1:
                    clean_ins1 = insights[1].lstrip("*-• ").strip()
                    # Lowercase first character if appropriate
                    if clean_ins1 and clean_ins1[0].isupper() and not clean_ins1.startswith("₹"):
                        clean_ins1 = clean_ins1[0].lower() + clean_ins1[1:]
                    response_text += f"Additionally, {clean_ins1}."

    log.info("responder.complete", response_length=len(response_text))

    final_response = {
        "text": response_text,
        "chart": state.get("viz_config") if row_count > 0 else None,
        "insights": insights[:3],
        "key_metrics": key_metrics,
        "anomalies": anomalies,
        "follow_up_questions": follow_ups,
        "sql": state.get("sql_query", ""),
        "sql_explanation": sql_explanation,
        "row_count": row_count,
        "viz_type": viz_type,
        "columns": query_results.get("columns", []),
        "rows": query_results.get("rows", [])[:200],
        "truncated": query_results.get("truncated", False),
    }

    # Run blocking database, vector, and storage writes in the thread pool via asyncio.to_thread to prevent blocking the event loop
    sql = state.get("sql_query")
    if sql and not state.get("error"):
        try:
            vector_memory.store_query(
                user_id=state.get("user_id", "anonymous"),
                question=state["user_question"],
                sql=sql,
                payload={
                    "datasource_id": state.get("datasource_id"),
                    "viz_type": viz_type,
                    "row_count": row_count
                }
            )
            log.debug("responder.stored_in_vector_memory", question=state["user_question"][:60])
        except Exception as e:
            log.warning("responder.vector_store_failed", error=str(e))

    # Store Q&A pair in QA Memory for fast retrieval
    if not state.get("error") and row_count > 0:
        try:
            from backend.services.knowledge.business_knowledge import get_qa_memory_service
            qa_service = get_qa_memory_service()
            qa_service.store_qa(
                question=state["user_question"],
                answer=response_text,
                sql=state.get("sql_query", ""),
                tables=_extract_tables_from_sql(sql) if sql else [],
                columns=query_results.get("columns", []),
                user_id=state.get("user_id", "anonymous"),
                viz_type=viz_type
            )
            log.debug("responder.stored_in_qa_memory", question=state["user_question"][:60])
        except Exception as e:
            log.warning("responder.qa_memory_store_failed", error=str(e))

    # Store conversation in MinIO for persistent history
    try:
        minio_conversation_store.save_conversation(
            user_id=state.get("user_id", "anonymous"),
            conversation_id=state.get("conversation_id", "default"),
            question=state["user_question"],
            response=final_response,
            session_id=state.get("session_id"),
        )
        log.debug("responder.stored_in_minio", user_id=state.get("user_id", "anonymous"))
    except Exception as e:
        log.warning("responder.minio_store_failed", error=str(e))

    return {**state, "response_text": response_text, "follow_up_questions": follow_ups, "final_response": final_response}


async def _generate_follow_ups(question: str, insights: list, viz_type: str, sql: str = "", columns: list = []) -> list[str]:
    """
    Generate follow-up questions that are GUARANTEED to be answerable by the system.
    Grounds suggestions in the actual database schema, known columns, and domain context.
    """
    import re

    # ── Extract time scope from SQL so follow-ups stay in the same period ──────
    year_hints = re.findall(r"'(20\d\d)(?:-\d\d)?(?:-\d\d)?'", sql)
    year_scope = ", ".join(sorted(set(year_hints))) if year_hints else "the same period as the query"

    # ── Extract which table(s) were queried ────────────────────────────────────
    tables_used = []
    for tname in ["combined_sales_final", "product_master", "inventory_sales_overview_new",
                   "shopify_orders", "unicomm_sales_final", "zoho_sales_final"]:
        if tname in sql.lower():
            tables_used.append(tname)
    tables_str = ", ".join(tables_used) if tables_used else "combined_sales_final"

    # ── Domain knowledge: always-available dimensions ─────────────────────────
    KNOWN_DIMENSIONS = """
AVAILABLE DIMENSIONS (always queryable via combined_sales_final + product_master JOIN):
- sales_platform: 'Nykaa Beauty', 'Myntra_PPMP', 'Shopify', 'Unicomm', 'Zoho'
- category_l1: 'Skincare', 'Makeup', 'Haircare'
- category_l2: sub-category (Face Wash, Serum, Foundation, etc.)
- item_name: individual product name (from product_master)
- internal_sku: unique product code
- date_created: order date (for monthly/daily grouping)
- final_status: order status (already filtered out cancelled/returned)

AVAILABLE METRICS:
- row_subtotal → revenue/sales (SUM)
- quantity_ordered → units sold (SUM)
- COUNT(*) → number of orders
"""

    cols_str = ", ".join(columns) if columns else "revenue, platform"

    prompt = f"""You are generating follow-up questions for an analytics dashboard about Limese beauty brand sales.

User's original question: "{question}"
SQL query used tables: {tables_str}
Columns in the result: {cols_str}
Time period filtered: {year_scope}
Chart shown: {viz_type}
Key insight: {insights[0] if insights else 'N/A'}

{KNOWN_DIMENSIONS}

Generate exactly 3 follow-up questions. Each question MUST:
1. Be directly answerable using the dimensions and metrics listed above via a single SQL query
2. Stay within the same time period ({year_scope}) unless asking for a comparison
3. Be a natural drill-down or comparison from the original question
4. Be specific — use real platform names, category names, or metric names from above (not vague phrases like "break this down further")
5. Be under 12 words

GOOD examples (specific, answerable):
- "Show revenue by category_l1 on Nykaa Beauty in {year_scope}"
- "Which product had highest units sold in {year_scope}?"
- "Compare Skincare vs Makeup revenue in {year_scope}"
- "Show monthly revenue trend for Nykaa Beauty in {year_scope}"

BAD examples (vague, unanswerable):
- "Break this down further" ← too vague
- "Show me more details" ← not specific
- "Compare with other regions" ← no region column exists

Return ONLY a JSON array of exactly 3 strings: ["question 1", "question 2", "question 3"]"""

    try:
        resp = await call_llm(
            messages=[{"role": "user", "content": prompt}],
            task="routing",
            max_tokens=200,
            temperature=0.2,
        )
        raw = resp.content.strip()
        if not raw:
            raise ValueError("Empty LLM response")
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()
        result = json.loads(raw)
        if isinstance(result, list) and len(result) >= 1:
            return [q for q in result if isinstance(q, str)][:3]
        raise ValueError("Invalid format")
    except Exception:
        return _default_follow_ups(question, year_scope)


def _default_follow_ups(question: str, year_scope: str = "2025") -> list[str]:
    """Deterministic fallback follow-ups — always answerable."""
    q_lower = question.lower()
    # Pick context-relevant fallbacks
    if "nykaa" in q_lower:
        return [
            f"Show Nykaa Beauty revenue by category in {year_scope}",
            f"Which product sold most on Nykaa in {year_scope}?",
            f"Compare Nykaa vs Myntra revenue in {year_scope}",
        ]
    if "myntra" in q_lower:
        return [
            f"Show Myntra revenue by category in {year_scope}",
            f"Compare Myntra vs Nykaa revenue in {year_scope}",
            f"Which SKU performed best on Myntra in {year_scope}?",
        ]
    if "skincare" in q_lower or "makeup" in q_lower or "haircare" in q_lower:
        return [
            f"Show monthly revenue trend by category in {year_scope}",
            f"Which platform drives most Skincare sales in {year_scope}?",
            f"Compare category revenue across platforms in {year_scope}",
        ]
    # Generic but always answerable defaults
    return [
        f"Show revenue by platform in {year_scope}",
        f"Which product had highest sales in {year_scope}?",
        f"Show monthly revenue trend in {year_scope}",
    ]




async def _compose_analytical_response(state: AnalyticsState) -> dict:
    """
    Compose a narrative, prose-based response for analytical questions.
    Uses the fetched data to ground the explanation in facts.
    """
    question = state["user_question"]
    query_results = state.get("query_results", {})
    insights = state.get("insights", [])
    key_metrics = state.get("key_metrics", {})
    row_count = query_results.get("row_count", 0)
    columns = query_results.get("columns", [])
    rows = query_results.get("rows", [])[:10]

    # Build data context for the LLM
    data_summary = f"""
Question: {question}

Data Retrieved ({row_count} rows):
Columns: {columns}

Sample Data:
{rows[:5]}

Key Metrics:
{key_metrics}

Insights Found:
{insights[:3]}
"""

    prompt = f"""You are a data analyst answering an analytical question.

{data_summary}

Provide a clear, conversational answer (2-3 paragraphs) that:
1. Directly addresses their "why/explain" question
2. Uses specific numbers from the data above
3. Provides context and interpretation
4. Is easy to understand (avoid technical jargon)

Format with markdown for readability. Use ₹ for currency.

After your explanation, suggest 2-3 specific follow-up questions."""

    try:
        resp = await call_llm(
            messages=[{"role": "user", "content": prompt}],
            task="analysis",
            max_tokens=600,
            temperature=0.4,
        )

        # Generate contextual follow-up questions
        follow_ups = await _generate_analytical_followups(question, key_metrics, insights)

        return {
            **state,
            "response_text": resp.content,
            "follow_up_questions": follow_ups,
            "final_response": {
                "text": resp.content,
                "chart": state.get("viz_config"),  # Still show chart if available
                "insights": insights,
                "key_metrics": key_metrics,
                "follow_up_questions": follow_ups,
                "sql": state.get("sql_query", ""),
                "row_count": row_count,
                "viz_type": state.get("viz_type"),
                "columns": columns,
                "rows": rows,
            }
        }
    except Exception as exc:
        log.warning("analytical_response.failed", error=str(exc))
        # Fallback to standard response
        return {
            **state,
            "response_text": f"Based on the data ({row_count} rows found), here are the key findings:\n\n" + "\n".join(f"* {i}" for i in insights[:3]),
            "follow_up_questions": _default_follow_ups(question),
        }


async def _generate_analytical_followups(question: str, key_metrics: dict, insights: list, sql: str = "") -> list[str]:
    """Delegate to the main schema-aware follow-up generator for consistency."""
    try:
        columns = list(key_metrics.keys())
        return await _generate_follow_ups(question, insights, "table", sql, columns)
    except Exception:
        return _default_follow_ups(question)


async def _compose_analytical_no_data_response(state: AnalyticsState) -> dict:
    """
    Compose a helpful response for analytical questions when no data is found.
    Instead of 'No data found', provides context and suggests alternatives.
    """
    question = state["user_question"]
    intent = state.get("intent", {})
    entities = intent.get("entities", [])

    # Build a contextual response
    if entities:
        entities_str = ", ".join(entities[:3])
        response_text = (
            f"I don't have specific data about **{entities_str}** in the current database, "
            f"or the combination of filters returned no results.\n\n"
            f"However, I can help you explore related questions. Would you like to:\n"
            f"• See overall revenue trends instead?\n"
            f"• Explore data at a broader level (e.g., by platform or category)?\n"
            f"• Check a different time period?"
        )
    else:
        response_text = (
            f"I couldn't find data directly addressing your question about \"{question}.\"\n\n"
            f"This could mean:\n"
            f"• The specific data points aren't in the database\n"
            f"• The filters are too restrictive\n"
            f"• The time period has no data\n\n"
            f"Try asking about:\n"
            f"• Overall revenue by platform\n"
            f"• Top products by sales\n"
            f"• Monthly sales trends"
        )

    # Generate contextual follow-up questions
    follow_ups = [
        "Show me overall revenue by platform",
        "What are the top selling products?",
        "Show monthly revenue trend",
    ]

    if entities:
        entity = entities[0]
        follow_ups.insert(0, f"Show me all data related to {entity}")
        follow_ups.insert(1, f"What do we know about {entity}?")

    return {
        **state,
        "response_text": response_text,
        "follow_up_questions": follow_ups,
        "final_response": {
            "text": response_text,
            "chart": None,
            "insights": [],
            "key_metrics": {},
            "follow_up_questions": follow_ups,
            "sql": state.get("sql_query", ""),
            "row_count": 0,
            "viz_type": None,
        },
    }


async def _compose_schema_info_response(question: str) -> dict:
    from backend.services.db_intelligence import get_db_context, PRIORITY_TABLES, TABLE_DESCRIPTIONS
    
    db_ctx = get_db_context()
    tables = db_ctx.get("tables", {})
    q_lower = question.lower()
    
    target_table = None
    
    # 1. Match exact table name
    for tname in tables.keys():
        t_clean = tname.lower().replace("_", " ").replace("-", " ")
        if tname.lower() in q_lower or t_clean in q_lower:
            target_table = tname
            break
            
    # 2. Match aliases or keywords
    if not target_table:
        for tname, tdata in tables.items():
            for alias in tdata.get("aliases", []):
                if alias.lower() in q_lower:
                    target_table = tname
                    break
            if target_table:
                break
                
    if target_table:
        tdata = tables[target_table]
        row_count = tdata.get("row_count", 0)
        cols = tdata.get("columns", [])
        desc = tdata.get("description") or TABLE_DESCRIPTIONS.get(target_table, "Operational dataset.")
        
        # Build premium table summary
        response_text = (
            f"### 📊 Schema Analysis: `{target_table}`\n\n"
            f"**Description:**\n{desc}\n\n"
            f"#### Table Statistics\n"
            f"- **Total Columns:** {len(cols)}\n"
            f"- **Estimated Row Count:** {row_count:,} rows\n\n"
            f"#### Column Definitions\n"
            f"| Column Name | Data Type | Business Description |\n"
            f"| :--- | :--- | :--- |\n"
        )
        
        for col in cols[:40]:  # limit to 40 columns for readable markdown response
            cname = col.get("name", "")
            ctype = col.get("type", "String")
            canno = col.get("annotation") or ""
            response_text += f"| `{cname}` | `{ctype}` | {canno or 'No annotation available'} |\n"
            
        if len(cols) > 40:
            response_text += f"\n*...and {len(cols) - 40} more columns. Use a targeted query to view them.*"
            
        # Contextual follow-up questions
        follow_ups = [
            f"Show me the first 5 rows of {target_table}",
            f"How many rows are in the {target_table} table?",
            f"Analyze sales and trends in {target_table}",
        ]
    else:
        # General catalog request: "show all tables"
        response_text = (
            f"### 🗃️ Database Table Catalog\n\n"
            f"Here are the key business tables available for querying inside the Limese ClickHouse database:\n\n"
            f"| Table Name | Description |\n"
            f"| :--- | :--- |\n"
        )
        for tname in PRIORITY_TABLES:
            if tname in tables:
                desc = TABLE_DESCRIPTIONS.get(tname, tables[tname].get("description", "Operational dataset."))
                response_text += f"| `{tname}` | {desc[:120]}... |\n"
                
        response_text += (
            f"\nThere are a total of **{len(tables)} tables** in the database. "
            f"The 12 tables listed above contain all core revenue, product, and inventory metrics. "
            f"You can query or analyze any of them by asking a direct question!"
        )
        
        follow_ups = [
            "Show me columns inside combined_sales_final",
            "What tables contain inventory metrics?",
            "Show monthly sales trend",
        ]
        
    return {
        "text": response_text,
        "chart": None,
        "insights": [],
        "key_metrics": {},
        "follow_up_questions": follow_ups,
        "sql": "",
        "row_count": 0,
        "viz_type": None,
    }

