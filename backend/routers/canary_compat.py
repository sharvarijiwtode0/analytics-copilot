"""
Canary-Compatible API Endpoints
Adds endpoints that match Canary's backend API so:
  1. Canary's frontend can plug directly into our DVC backend
  2. Our DVC gets richer metadata for better SQL generation

Endpoints added (matching Canary's contract exactly):
  GET  /clickhouse/tables/all
  GET  /clickhouse/tables
  GET  /clickhouse/metadata/{table}
  GET  /clickhouse/metadata/{table}/generate
  GET  /clickhouse/column-distribution/{table}/{col}
  POST /clickhouse/generate-sql
  POST /clickhouse/query
  GET  /clickhouse/health
  GET  /cache/stats
  POST /cache/clear
"""
from __future__ import annotations

from typing import Any, Optional

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.data.connector import execute_query, get_schema
from backend.services.metadata_engine import (
    generate_table_metadata,
    load_cached_metadata,
    list_cached_tables,
    build_llm_schema_context,
)
from backend.services.llm_cache import get_cache
from backend.agent.llm import call_llm

log = structlog.get_logger(__name__)
router = APIRouter()

# The Limese ClickHouse datasource ID
LIMESE_DS = "limese"


async def _ch_execute(sql: str) -> dict:
    """Execute a SQL query against Limese ClickHouse."""
    return await execute_query(LIMESE_DS, sql)


# ─── Tables ───────────────────────────────────────────────────────────────────

@router.get("/clickhouse/tables/all")
async def list_all_tables() -> dict:
    """
    List ALL tables in the Limese ClickHouse database with metadata status.
    Matches Canary's GET /clickhouse/tables/all contract exactly.
    """
    schema = await get_schema(LIMESE_DS)
    cached = {t["table_name"]: t for t in list_cached_tables(LIMESE_DS)}

    tables_info = []
    for table in schema.get("tables", []):
        name = table["name"]
        row_count = table.get("row_count", 0)
        col_count = len(table.get("columns", []))
        has_meta = name in cached

        info: dict = {
            "name": name,
            "has_metadata": has_meta,
            "total_columns": col_count,
        }
        if has_meta:
            info["total_rows"] = cached[name].get("total_rows", row_count)
            info["generated_at"] = cached[name].get("generated_at")
        else:
            info["total_rows"] = row_count

        tables_info.append(info)

    return {"success": True, "tables": tables_info, "count": len(tables_info)}


@router.get("/clickhouse/tables")
async def list_tables_with_metadata() -> dict:
    """List only tables that have pre-generated metadata (matches Canary)."""
    cached = list_cached_tables(LIMESE_DS)
    return {"success": True, "tables": cached, "count": len(cached)}


# ─── Metadata ─────────────────────────────────────────────────────────────────

@router.get("/clickhouse/metadata/{table_name}/generate")
async def generate_metadata(table_name: str) -> dict:
    """
    Generate rich column-level metadata for a table.
    Runs SQL to compute per-column: unique count, non-null count, sample values,
    numerical/date ranges. Caches to /tmp. SLOW first call, instant after.
    """
    try:
        metadata = await generate_table_metadata(
            datasource_id=LIMESE_DS,
            table_name=table_name,
            execute_fn=_ch_execute,
            force_refresh=True,
        )
        return {"success": True, **metadata}
    except Exception as exc:
        log.error("metadata.generate_failed", table=table_name, error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/clickhouse/metadata/{table_name}")
async def get_metadata(table_name: str) -> dict:
    """
    Get cached metadata for a table. If not cached, returns 404.
    Use /generate to create it first.
    """
    meta = load_cached_metadata(LIMESE_DS, table_name)
    if not meta:
        raise HTTPException(
            status_code=404,
            detail=f"No cached metadata for '{table_name}'. Call /generate first."
        )
    return {"success": True, **meta}


# ─── Column Distribution ───────────────────────────────────────────────────────

@router.get("/clickhouse/column-distribution/{table_name}/{column_name}")
async def get_column_distribution(table_name: str, column_name: str) -> dict:
    """
    Get detailed distribution stats for a specific column.
    Matches Canary's column-distribution endpoint.
    Returns numerical stats, temporal range, or categorical distribution.
    """
    try:
        # Get column type from schema
        schema = await get_schema(LIMESE_DS)
        col_info = None
        for table in schema.get("tables", []):
            if table["name"] == table_name:
                for col in table.get("columns", []):
                    if col["name"] == column_name:
                        col_info = col
                        break

        if not col_info:
            raise HTTPException(404, f"Column '{column_name}' not found in '{table_name}'")

        ch_type = col_info.get("type", "String")
        ch_type_lower = ch_type.lower()

        # Classify type
        if any(x in ch_type_lower for x in ["int", "float", "decimal"]):
            dist_type = "numerical"
        elif any(x in ch_type_lower for x in ["date", "datetime"]):
            dist_type = "temporal"
        else:
            dist_type = "text"

        # Total rows + unique/null counts
        base_result = await _ch_execute(f"""
            SELECT
                count() as total_rows,
                uniq(`{column_name}`) as unique_count,
                countIf(`{column_name}` IS NULL) as null_count
            FROM {table_name}
        """)
        base = base_result["rows"][0] if base_result.get("rows") else {}
        total_rows = int(base.get("total_rows", 0) or 0)
        unique_count = int(base.get("unique_count", 0) or 0)
        null_count = int(base.get("null_count", 0) or 0)
        null_pct = round(null_count / total_rows * 100, 2) if total_rows > 0 else 0

        result: dict = {
            "column_name": column_name,
            "datatype": ch_type,
            "distribution_type": dist_type,
            "statistics": {
                "unique_count": unique_count,
                "null_count": null_count,
                "null_percentage": null_pct,
                "total_rows": total_rows,
            },
        }

        # Numerical stats
        if dist_type == "numerical":
            num_result = await _ch_execute(f"""
                SELECT
                    min(`{column_name}`) as min_val,
                    max(`{column_name}`) as max_val,
                    avg(`{column_name}`) as mean_val,
                    median(`{column_name}`) as median_val,
                    stddevPop(`{column_name}`) as std_dev
                FROM {table_name}
                WHERE `{column_name}` IS NOT NULL
            """)
            if num_result.get("rows"):
                n = num_result["rows"][0]
                result["statistics"].update({
                    "min": float(n.get("min_val") or 0),
                    "max": float(n.get("max_val") or 0),
                    "mean": round(float(n.get("mean_val") or 0), 2),
                    "median": round(float(n.get("median_val") or 0), 2),
                    "std_dev": round(float(n.get("std_dev") or 0), 2),
                })

        # Temporal range
        elif dist_type == "temporal":
            temp_result = await _ch_execute(f"""
                SELECT
                    toString(min(`{column_name}`)) as earliest,
                    toString(max(`{column_name}`)) as latest,
                    dateDiff('day', min(`{column_name}`), max(`{column_name}`)) as span_days
                FROM {table_name}
                WHERE `{column_name}` IS NOT NULL
            """)
            if temp_result.get("rows"):
                t = temp_result["rows"][0]
                result["temporal_range"] = {
                    "earliest": str(t.get("earliest", "")),
                    "latest": str(t.get("latest", "")),
                    "time_span_days": int(t.get("span_days") or 0),
                }

        # Categorical distribution (if < 200 unique values)
        if unique_count < 200:
            result["distribution_type"] = "categorical"
            dist_result = await _ch_execute(f"""
                SELECT `{column_name}` as category, count() as count
                FROM {table_name}
                WHERE `{column_name}` IS NOT NULL AND `{column_name}` != ''
                GROUP BY `{column_name}`
                ORDER BY count DESC
                LIMIT 100
            """)
            result["distribution_data"] = [
                {"category": str(r.get("category", "")), "count": r.get("count", 0)}
                for r in dist_result.get("rows", [])
            ]

        return result

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ─── SQL Generation (Canary-style with metadata context) ──────────────────────

class ConversationEntry(BaseModel):
    question: str
    sql_query: str
    result_sample: Optional[list] = None
    visualization_type: Optional[str] = None


class LLMQueryRequest(BaseModel):
    question: str
    table_name: str
    conversation_history: Optional[list[ConversationEntry]] = []


@router.post("/clickhouse/generate-sql")
async def generate_sql(request: LLMQueryRequest) -> dict:
    """
    Natural language → ClickHouse SQL using metadata context.
    Mirrors Canary's POST /clickhouse/generate-sql.
    Uses our Groq LLM + metadata engine + LLM cache.
    """
    cache = get_cache()

    # Check cache first
    cached_result = cache.get(
        question=request.question,
        datasource_id=f"{LIMESE_DS}/{request.table_name}",
    )
    if cached_result:
        cached_result["from_cache"] = True
        return cached_result

    # Load or generate metadata
    meta = load_cached_metadata(LIMESE_DS, request.table_name)
    if not meta:
        try:
            meta = await generate_table_metadata(
                datasource_id=LIMESE_DS,
                table_name=request.table_name,
                execute_fn=_ch_execute,
            )
        except Exception as exc:
            raise HTTPException(status_code=404, detail=f"Could not load metadata for {request.table_name}: {exc}")

    # Build schema context (Canary-style with possible values)
    schema_context = build_llm_schema_context(meta)

    # Build system prompt
    system_prompt = f"""You are a ClickHouse SQL expert generating queries for a data visualization tool.

{schema_context}

CLICKHOUSE RULES:
- Use backticks for column names: `column_name`
- ClickHouse functions: sum(), avg(), count(), uniq(), toDate(), formatDateTime(), ifNull()
- Always add LIMIT (100 for detail, 20 for aggregations)
- For date filtering: use >= '2025-01-01' format, not toYear() comparison
- For revenue: use row_subtotal (not order_price)
- For units: use quantity_ordered (not shipped_qty)

OUTPUT — Return JSON only:
{{
  "sql_query": "SELECT ...",
  "visualization_type": "bar|line|pie|table",
  "title": "Chart title under 60 chars",
  "description": "One sentence explanation",
  "category_key": "x-axis or label column name",
  "value_keys": ["y-axis column names"]
}}"""

    # Build messages with conversation history (Canary pattern)
    messages = [{"role": "system", "content": system_prompt}]

    if request.conversation_history:
        for entry in request.conversation_history[-5:]:
            messages.append({"role": "user", "content": entry.question})
            assistant_msg = f"Generated SQL:\n```sql\n{entry.sql_query}\n```"
            if entry.result_sample:
                import json
                assistant_msg += f"\n\nSample results:\n```json\n{json.dumps(entry.result_sample[:3], default=str)}\n```"
            if entry.visualization_type:
                assistant_msg += f"\n\nVisualization: {entry.visualization_type}"
            messages.append({"role": "assistant", "content": assistant_msg})

    messages.append({"role": "user", "content": request.question})

    # Call LLM
    try:
        llm_resp = await call_llm(messages, task="sql", max_tokens=800, temperature=0.1)
        raw = llm_resp.content.strip()

        # Parse JSON (handle markdown fences)
        if "```" in raw:
            parts = raw.split("```")
            for p in parts:
                p = p.replace("json", "").strip()
                if p.startswith("{"):
                    raw = p
                    break

        import json as _json
        parsed = _json.loads(raw)

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"LLM generation failed: {exc}")

    result = {
        "sql_query": parsed.get("sql_query", ""),
        "visualization_type": parsed.get("visualization_type", "table"),
        "title": parsed.get("title", request.question[:60]),
        "description": parsed.get("description", ""),
        "category_key": parsed.get("category_key"),
        "value_keys": parsed.get("value_keys", []),
        "model_used": llm_resp.model,
        "from_cache": False,
    }

    # Cache result
    cache.set(
        question=request.question,
        datasource_id=f"{LIMESE_DS}/{request.table_name}",
        result=result,
        metadata_hash=meta.get("metadata_hash", ""),
    )

    return result


# ─── Query Execution ──────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    query: str
    format: str = "records"


@router.post("/clickhouse/query")
async def execute_ch_query(request: QueryRequest) -> dict:
    """Execute SQL directly against Limese ClickHouse (matches Canary contract)."""
    try:
        result = await _ch_execute(request.query)
        return {
            "success": True,
            "columns": result.get("columns", []),
            "data": result.get("rows", []),
            "row_count": result.get("row_count", len(result.get("rows", []))),
            "query": request.query,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ─── Health ───────────────────────────────────────────────────────────────────

@router.get("/clickhouse/health")
async def clickhouse_health() -> dict:
    """Check Limese ClickHouse connection health."""
    try:
        result = await _ch_execute("SELECT count() as cnt FROM combined_sales_final LIMIT 1")
        rows = result.get("row_count", 0)
        return {
            "status": "connected",
            "datasource": LIMESE_DS,
            "host": f"{settings.clickhouse_host}:{settings.clickhouse_port}",
            "database": settings.clickhouse_database,
            "test_query_rows": rows,
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


# ─── Cache management ─────────────────────────────────────────────────────────

@router.get("/cache/stats")
async def cache_stats() -> dict:
    """LLM cache statistics."""
    return get_cache().stats()


@router.post("/cache/clear")
async def cache_clear() -> dict:
    """Clear LLM response cache."""
    get_cache().clear()
    return {"status": "cleared"}
