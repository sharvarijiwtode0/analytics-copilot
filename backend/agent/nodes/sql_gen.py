"""
Node 3: SQL Generation & Validation
Generates ClickHouse SQL from the question + database schema context.
Uses the smart model (Groq 70B / Gemini fallback) for accuracy.

Role boundary: ONLY generates and validates SQL.
Does NOT execute queries. Does NOT generate insights or charts.
"""
from __future__ import annotations
import json
import re
import structlog
from backend.agent.state import AnalyticsState
from backend.agent.llm import call_llm
from backend.agent.memory import vector_memory
from backend.agent.utils import resilient_json_loads
from backend.config import settings
from backend.services.business_rag import build_rag_prompt

log = structlog.get_logger(__name__)


def _clean_sql(sql: str) -> str:
    """Strip trailing JSON artifacts that models sometimes append after the SQL."""
    # Remove trailing quote + JSON keys (Groq sometimes leaks these)
    for pattern in [
        r'(SELECT[\s\S]+?LIMIT\s+\d+)\s*"[\s\S]*$',
        r'(SELECT[\s\S]+?)\s*",\s*"(?:explanation|columns|is_aggregated)',
        r'(SELECT[\s\S]+?)\s*;\s*"[\s\S]*$',
    ]:
        m = re.search(pattern, sql, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return sql.strip()


def _parse_sql_from_llm(content: str) -> tuple[str, dict]:
    """Robust parser for LLM SQL responses — handles JSON, markdown fences, and raw SQL."""
    raw = content.strip()
    result: dict = {"explanation": "", "columns_returned": []}

    def _extract_json_object(text: str) -> dict | None:
        """Find and parse the outermost JSON object in text."""
        # Find the first '{' and last '}' — handles nested braces in SQL strings
        start = text.find('{')
        if start == -1:
            return None
        # Walk forward counting braces to find the matching '}'
        depth = 0
        in_string = False
        escape_next = False
        for i, ch in enumerate(text[start:], start):
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
            if not in_string:
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i+1])
                        except Exception:
                            return None
        return None

    # 1. Try JSON parse — strip markdown fences first
    search_text = raw
    if "```" in raw:
        # Remove fences to get the inner content
        inner = re.sub(r'```(?:json)?', '', raw).replace('```', '').strip()
        search_text = inner

    parsed = _extract_json_object(search_text)
    if parsed and isinstance(parsed, dict):
        sql = parsed.get("sql", "").strip()
        # Unescape JSON-escaped newlines
        sql = sql.replace("\\n", "\n").replace('\\"', '"')
        result["explanation"] = parsed.get("explanation", "")
        result["columns_returned"] = parsed.get("columns_returned", [])
        if sql:
            return _clean_sql(sql), result

    # 2. Fallback: find raw SELECT…LIMIT block
    sql_match = re.search(r'(SELECT[\s\S]+?(?:LIMIT\s+\d+|;|$))', raw, re.IGNORECASE)
    if sql_match:
        sql = _clean_sql(sql_match.group(1))
        if len(sql) > 15:
            return sql, result

    return "", result


# ─── READ-ONLY ENFORCEMENT ─────────────────────────────────────────────────────
# Multiple layers of defense to prevent ANY data modification
# ────────────────────────────────────────────────────────────────────────────────

# Patterns that are NEVER allowed under any circumstances
FORBIDDEN_PATTERNS = [
    # Data modification
    r"\bDROP\b", r"\bDELETE\b", r"\bTRUNCATE\b", r"\bALTER\b",
    r"\bCREATE\s+(TABLE|INDEX|VIEW|DATABASE|SCHEMA)\b",
    r"\bINSERT\b", r"\bUPDATE\b", r"\bREPLACE\b", r"\bMERGE\b",
    # Privilege escalation
    r"\bGRANT\b", r"\bREVOKE\b", r"\bSET\s+ROLE\b", r"\bSET\s+SESSION\b",
    # Execution
    r"\bEXEC\b", r"\bEXECUTE\b", r"\bEVAL\b", r"\bSYSTEM\b",
    # File operations
    r"\bINTO\s+OUTFILE\b", r"\bINTO\s+DUMPFILE\b", r"\bLOAD\s+DATA\b",
    # Shell/OS access
    r"\bsystem\b", r"\bexec\b", r"\bpopen\b", r"\bshell\b",
    # Transaction control (no commits)
    r"\bCOMMIT\b", r"\bROLLBACK\b", r"\bBEGIN\b", r"\bSTART\s+TRANSACTION\b",
    # Comment tricks (SQL injection)
    r";--", r";#", r"\/\*.*\*\/;.*SELECT", r"--.*;.*DROP",
    # Multi-statement attempts
    r";.*\b(SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE)\b",
]

# ALLOWLIST: Only these keywords are permitted in queries
ALLOWED_KEYWORDS = {
    "SELECT", "FROM", "WHERE", "GROUP", "BY", "ORDER", "HAVING",
    "LIMIT", "OFFSET", "AND", "OR", "NOT", "IN", "EXISTS", "BETWEEN",
    "LIKE", "ILIKE", "IS", "NULL", "AS", "DISTINCT", "WITH", "CASE",
    "WHEN", "THEN", "ELSE", "END", "JOIN", "INNER", "LEFT", "RIGHT",
    "FULL", "OUTER", "CROSS", "ON", "UNION", "INTERSECT", "EXCEPT",
    "CAST", "EXTRACT", "FORMAT", "DATE", "TIME", "TIMESTAMP", "INTERVAL",
    "SUM", "AVG", "COUNT", "MIN", "MAX", "STDDEV", "VARIANCE",
    "ROW_NUMBER", "RANK", "DENSE_RANK", "LAG", "LEAD", "FIRST", "LAST",
    "ARRAY", "ARRAYJOIN", "IF", "IFNULL", "COALESCE", "NULLIF",
    "toFixed", "toString", "toDate", "toDateTime", "formatDateTime",
    "lagInFrame", "leadInFrame",  # ClickHouse specific
}


def _is_safe_sql(sql: str) -> tuple[bool, str]:
    """
    STRICT READ-ONLY VALIDATION - Multiple security layers.
    Returns: (is_safe, error_message)
    """
    if not sql:
        return False, "Empty SQL query"

    upper_sql = sql.upper()
    original_sql = sql

    # ─── LAYER 1: Must start with SELECT ─────────────────────────────────────
    if not upper_sql.strip().startswith("SELECT"):
        return False, "Query must start with SELECT (read-only access only)"

    # ─── LAYER 2: Check forbidden patterns ───────────────────────────────────
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, upper_sql, re.IGNORECASE | re.MULTILINE):
            dangerous_word = pattern.replace(r"\b", "").replace(r"\s+", " ")
            log.warning("sql.forbidden_pattern_detected",
                       pattern=pattern,
                       sql_preview=original_sql[:200])
            return False, f"Forbidden operation detected: {dangerous_word}. Read-only access cannot be bypassed."

    # ─── LAYER 3: No semi-colons followed by keywords (multi-statement) ───────
    if ";" in sql:
        parts = sql.split(";")
        if len(parts) > 1:
            for part in parts[1:]:  # Check everything after first ;
                if part.strip() and any(kw in part.upper() for kw in ["SELECT", "INSERT", "UPDATE", "DELETE", "DROP", "CREATE"]):
                    return False, "Multi-statement queries not allowed"

    # ─── LAYER 4: Check for comment injection attempts ───────────────────────
    if "--" in sql and ";" in sql.split("--")[0]:
        return False, "Possible SQL injection via comments"

    # ─── LAYER 5: No function calls that could execute code ───────────────────
    dangerous_functions = [
        r"\bextension_", r"\bload_", r"\bbe_", r"\bcmdshell\b",
        r"\bsystem\b", r"\bexec\b", r"\b eval", r"\bfile_",
    ]
    for func in dangerous_functions:
        if re.search(func, upper_sql):
            return False, f"Dangerous function detected: {func}"

    log.info("sql.validated", sql_hash=hash(sql), length=len(sql))
    return True, "OK"


def _build_universal_schema_prompt(relevant_tables: list[dict]) -> str:
    """
    Constructs a detailed schema context prompt for non-ClickHouse or fallback datasources,
    representing tables, columns, types, row count, null count, and sample data.
    """
    if not relevant_tables:
        return "No tables available in schema context."
        
    lines = ["=== DATABASE SCHEMA ==="]
    for table in relevant_tables:
        tname = table.get("name", "")
        row_count = table.get("row_count")
        row_count_str = f" ({row_count:,} rows)" if row_count is not None else ""
        desc = table.get("description", "")
        desc_str = f" - {desc}" if desc else ""
        
        lines.append(f"\nTABLE: {tname}{row_count_str}{desc_str}")
        
        columns = table.get("columns", [])
        for col in columns:
            col_name = col.get("name", "")
            col_type = col.get("type", "")
            nullable = col.get("nullable", False)
            pk = col.get("primary_key", False)
            null_count = col.get("null_count", 0)
            
            col_meta = []
            if pk:
                col_meta.append("PRIMARY KEY")
            if nullable:
                col_meta.append("nullable")
            if null_count > 0:
                col_meta.append(f"null_count: {null_count}")
            
            meta_str = f" ({', '.join(col_meta)})" if col_meta else ""
            annotation = col.get("annotation", "")
            annotation_str = f"\n      ↳ {annotation}" if annotation else ""
            
            lines.append(f"  • `{col_name}` ({col_type}){meta_str}{annotation_str}")
            
        sample_data = table.get("sample_data", [])
        if sample_data:
            lines.append("  Sample Rows:")
            for row in sample_data[:2]:
                lines.append(f"    {json.dumps(row, default=str)}")
                
    return "\n".join(lines)


async def generate_sql(state: AnalyticsState) -> AnalyticsState:
    is_retry = state.get("sql_retry_count", 0) > 0
    # Skip if SQL already provided by semantic cache AND not a retry
    if state.get("sql_query") and not is_retry:
        return state

    intent = state.get("intent", {})
    schema_context = state.get("schema_context", {})
    question = intent.get("rephrased_question") or state.get("user_question", "")

    tables = schema_context.get("relevant_tables", [])
    datasource_id = state.get("datasource_id", "")
    is_clickhouse = datasource_id == "limese"

    from backend.agent.nodes.dynamic_schema import DynamicSchemaAgent
    schema_agent = DynamicSchemaAgent()

    # Interactively expand relevant tables if we detect keywords in question (0ms overhead)
    tables_in_context = [t.get("name") for t in tables if t.get("name")]
    expanded_table_names = schema_agent.scan_question_for_missing_tables(question, tables_in_context)
    
    if len(expanded_table_names) > len(tables_in_context):
        log.info("sql_gen.schema_expanded_interactively", original=tables_in_context, expanded=expanded_table_names)
        tables = []
        db_ctx = schema_agent.context.get("tables", {})
        for name in expanded_table_names:
            if name in db_ctx:
                tables.append({
                    "name": name,
                    "columns": db_ctx[name].get("columns", []),
                    "sample_data": db_ctx[name].get("sample_data"),
                    "row_count": db_ctx[name].get("row_count"),
                    "description": db_ctx[name].get("description", ""),
                })

    # Load DB intelligence context (cached on disk — instant load)
    db_intelligence_context = ""
    if is_clickhouse:
        try:
            from backend.services.db_intelligence import get_db_context, build_sql_context_prompt
            relevant_table_names = [t.get("name") for t in tables if t.get("name")]
            ctx = get_db_context()
            db_intelligence_context = build_sql_context_prompt(ctx, question, relevant_table_names)
        except Exception as exc:
            log.warning("sql_gen.db_intelligence_failed", error=str(exc))

    schema_section = db_intelligence_context if db_intelligence_context else _build_universal_schema_prompt(tables)

    q_lower = question.lower()
    has_units_keyword = any(w in q_lower for w in ["unit", "qty", "quantity", "volume", "order", "count"])
    has_revenue_keyword = any(w in q_lower for w in ["sales", "revenue", "subtotal", "price", "spend", "value", "amount", "earning", "profit"])

    # Fetch past successful queries from Qdrant for few-shot examples
    similar_queries = vector_memory.search_similar_queries(question, user_id=state.get("user_id", "anonymous"), limit=2)
    examples_str = ""
    if similar_queries:
        filtered_examples = []
        for q in similar_queries:
            ex_sql = q.get("sql", "").lower()
            ex_selects_units = "quantity_ordered" in ex_sql or "units" in ex_sql
            ex_selects_revenue = "row_subtotal" in ex_sql or "revenue" in ex_sql
            
            # If the user question does NOT ask for units, filter out any example SQL that selects units!
            if not has_units_keyword and ex_selects_units:
                continue
            # If the user question does NOT ask for revenue, filter out any example SQL that selects revenue!
            if not has_revenue_keyword and ex_selects_revenue:
                continue
            filtered_examples.append(q)
            
        if filtered_examples:
            examples_str = "\nPAST VERIFIED EXAMPLES (adapt, do not blindly copy):\n"
            for q in filtered_examples:
                if q.get("sql") and q.get("question"):
                    examples_str += f"Q: {q['question']}\nSQL: {q['sql']}\n\n"

    clickhouse_rules = """
CLICKHOUSE-SPECIFIC RULES (violations cause runtime errors):
1. Table aliases MUST be declared: write "FROM combined_sales_final csf" not just "FROM combined_sales_final".
   Then reference columns as csf.date_created, csf.row_subtotal, etc.
2. Window functions: use lagInFrame() / leadInFrame() — NOT lag() / lead().
3. Date filtering: date_created >= '2025-01-01' (string comparison works; toYear() with timezone fails).
4. Date grouping: formatDateTime(date_created, '%Y-%m') AS month.
5. Always filter: WHERE final_status NOT IN ('cancelled','Cancelled','CANCELLED','returned','Returned').
6. Revenue column: row_subtotal (NOT order_price).
7. Units column: quantity_ordered (NOT shipped_qty — always 0).
8. JOIN pattern: combined_sales_final csf LEFT JOIN product_master pm ON csf.internal_sku = pm.internal_sku.
9. Category filter: pm.category_l1 IN ('Skincare', 'Makeup', 'Haircare').
10. Always end with LIMIT (max 10000 for detail queries, 50-500 for aggregations).
11. STRICT DATE PERIOD FILTERING: If the user asks for a specific year (e.g. "in 2025"), you MUST include both start and end filters for that exact year, e.g. date_created >= '2025-01-01' AND date_created <= '2025-12-31'. Never let the dates spill into other years.
12. SINGLE-MONTH TREND QUERY PATTERN: If the user asks specifically for the "trend" of a single month (e.g., "sales trend for January 2025" or "trend in Jan"), you MUST group the sales by day (e.g., `formatDateTime(csf.date_created, '%Y-%m-%d') AS date`) so that multiple rows are returned to plot a daily trend line chart. Do NOT group by month or sum into a single row when a trend is explicitly requested.
13. ClickHouse Aggregate & GROUP BY Safety: In ClickHouse, every column in the SELECT list that is not a metric/measure MUST be in the GROUP BY clause, and all metric/measure columns (like `row_subtotal` or `quantity_ordered`) MUST be wrapped in aggregate functions (like `SUM(csf.row_subtotal)` or `SUM(csf.quantity_ordered)`). Never select raw `row_subtotal` without an aggregate function if GROUP BY is present.



CRITICAL COLUMN SELECTION RULES:
- ONLY select metric columns that are EXPLICITLY requested in the user's question.
- If the user asks for "sales", "revenue", "sales trend", or "performance", select ONLY the revenue column (`row_subtotal`). Do NOT select `quantity_ordered` (units) or other metrics unless specifically requested (e.g. if the user says "revenue and units" or "sales and volume").
- If the user asks for "units", "orders", "volume", or "quantity", select ONLY the quantity column (`quantity_ordered`). Do NOT select `row_subtotal` (revenue) unless specifically requested.
- Keeping the query focused prevents visual noise and keeps the dashboard extremely clean.
""" if is_clickhouse else ""

    # ─── RAG: Add business context for better SQL generation ─────────────────────
    rag_context = build_rag_prompt(question)

    extra_warning = ""
    if has_revenue_keyword and not has_units_keyword:
        extra_warning = "\n⚠️ COLUMN SELECTION WARNING: The user is asking ONLY for revenue/sales. You MUST NOT select 'quantity_ordered' (units) or alias anything as 'units'. Select ONLY the revenue metric column (row_subtotal AS revenue). Do not use the units column at all in this query.\n"
    elif has_units_keyword and not has_revenue_keyword:
        extra_warning = "\n⚠️ COLUMN SELECTION WARNING: The user is asking ONLY for units/quantity/orders. You MUST NOT select 'row_subtotal' (revenue) or alias anything as 'revenue'. Select ONLY the units metric column (quantity_ordered AS units).\n"

    # Dynamic date range parser (Month, Year, Multiple Years)
    years = [int(y) for y in re.findall(r'\b(20[12]\d)\b', question)]
    
    month_names = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
        'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }
    
    days_in_month = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}
    
    q_lower = question.lower()
    found_months = []
    for name, num in month_names.items():
        if re.search(r'\b' + name + r'\b', q_lower):
            found_months.append((name, num))
            
    # Check for digit months like MM-YYYY or YYYY-MM
    digit_month_match = re.search(r'\b(0[1-9]|1[0-2])[-/](20[12]\d)\b', question)
    if not digit_month_match:
        digit_month_match = re.search(r'\b(20[12]\d)[-/](0[1-9]|1[0-2])\b', question)
        
    if digit_month_match:
        if len(digit_month_match.group(1)) == 2:
            m_num = int(digit_month_match.group(1))
            yr = int(digit_month_match.group(2))
        else:
            yr = int(digit_month_match.group(1))
            m_num = int(digit_month_match.group(2))
        
        is_leap = (yr % 4 == 0 and (yr % 100 != 0 or yr % 400 == 0))
        last_day = 29 if (m_num == 2 and is_leap) else days_in_month[m_num]
        start_dt = f"{yr}-{m_num:02d}-01"
        end_dt = f"{yr}-{m_num:02d}-{last_day:02d}"
        extra_warning += f"\n⚠️ STRICT DATE FILTERING WARNING: The user asked specifically for month {m_num:02d}-{yr}. You MUST restrict the ClickHouse date range strictly to this month: `date_created >= '{start_dt}' AND date_created <= '{end_dt}'`. Do NOT select any data from other months.\n"
        
    elif found_months and years:
        m_name, m_num = found_months[0]
        yr = years[0]
        is_leap = (yr % 4 == 0 and (yr % 100 != 0 or yr % 400 == 0))
        last_day = 29 if (m_num == 2 and is_leap) else days_in_month[m_num]
        start_dt = f"{yr}-{m_num:02d}-01"
        end_dt = f"{yr}-{m_num:02d}-{last_day:02d}"
        extra_warning += f"\n⚠️ STRICT DATE FILTERING WARNING: The user asked specifically for {m_name.capitalize()} {yr}. You MUST restrict the ClickHouse date range strictly to this month: `date_created >= '{start_dt}' AND date_created <= '{end_dt}'`. Do NOT select any data from other months.\n"
        
    elif len(years) >= 2:
        start_yr = min(years)
        end_yr = max(years)
        extra_warning += f"\n⚠️ STRICT DATE FILTERING WARNING: The user asked for multiple years from {start_yr} to {end_yr}. You MUST restrict the ClickHouse date range strictly to this range: `date_created >= '{start_yr}-01-01' AND date_created <= '{end_yr}-12-31'`. Do NOT select any data from other years.\n"
        
    elif len(years) == 1:
        yr = years[0]
        extra_warning += f"\n⚠️ STRICT DATE FILTERING WARNING: The user asked specifically for the year {yr}. You MUST restrict the ClickHouse date range strictly to this year: `date_created >= '{yr}-01-01' AND date_created <= '{yr}-12-31'`. Do NOT select any data from other years.\n"

    retry_context = ""
    if is_retry and state.get("error"):
        failed_sql = state.get("sql_query", "")
        error_msg = state.get("error", "")
        
        # Use DynamicSchemaAgent to parse error and provide corrective advice!
        error_resolution = schema_agent.resolve_execution_error(error_msg, failed_sql)
        resolution_hint = f"\n💡 SCHEMA CORRECTION ADVICE: {error_resolution}\n" if error_resolution else ""
        
        retry_context = f"""
❌ RETRY ATTEMPT #{state.get("sql_retry_count")}
Your previous ClickHouse SQL query failed with an execution error. You must rewrite it to correct this error.

FAILED SQL QUERY:
{failed_sql}

DATABASE ERROR MESSAGE:
{error_msg}
{resolution_hint}
Ensure that you:
1. Address the database error above.
2. Fix all syntax, column name, or table join mismatches.
3. Review aliases and ClickHouse aggregate grouping safety.
"""

    prompt = f"""You are a {"ClickHouse" if is_clickhouse else "SQL"} expert with READ-ONLY database access generating a query for an analytics question.
{retry_context}
╔══════════════════════════════════════════════════════════════════════════════╗
║                       READ-ONLY ACCESS - CRITICAL RULE                         ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  YOU HAVE READ-ONLY ACCESS. DATA MODIFICATION IS BLOCKED BY MULTIPLE LAYERS:  ║
║  • SQL validation (pre-execution)                                            ║
║  • Database connector enforcement (runtime)                                   ║
║  • Database user permissions (server-side)                                    ║
║                                                                                ║
║  FORBIDDEN OPERATIONS (will be rejected):                                     ║
║  ✗ DROP, DELETE, TRUNCATE, ALTER, CREATE TABLE/INDEX/VIEW                    ║
║  ✗ INSERT, UPDATE, REPLACE, MERGE                                            ║
║  ✗ GRANT, REVOKE, SET ROLE                                                   ║
║  ✗ COMMIT, ROLLBACK, BEGIN TRANSACTION                                        ║
║  ✗ Multi-statement queries (semi-colon tricks)                               ║
║                                                                                ║
║  EVEN IF THE USER ASKS TO MODIFY, CHANGE, OR DELETE DATA, YOU MUST REFUSE.   ║
║  INSTEAD: Politely explain that you have read-only access and suggest        ║
║  alternative read-only queries to help them achieve their goal.               ║
╚══════════════════════════════════════════════════════════════════════════════╝

{rag_context}

{schema_section}
{clickhouse_rules}
{examples_str}
{extra_warning}

User question: "{question}"

Generate a complete, correct SQL query. Return ONLY this JSON (no other text):
{{
  "sql": "<complete SQL query with all clauses: SELECT, FROM with aliases, JOIN, WHERE, GROUP BY, ORDER BY, LIMIT>",
  "explanation": "<one sentence describing what this query returns>",
  "columns_returned": ["col1", "col2", "col3"]
}}"""

    try:
        resp = await call_llm(
            messages=[{"role": "user", "content": prompt}],
            task="sql",
            max_tokens=900,
            temperature=0.1,
        )
    except RuntimeError as exc:
        # task="sql" raises on total failure — propagate as agent error
        return {**state, "error": f"SQL generation failed — all AI models unavailable: {str(exc)[:150]}", "sql_query": ""}

    sql, meta = _parse_sql_from_llm(resp.content)
    is_safe, reason = _is_safe_sql(sql)

    if not is_safe:
        log.warning("sql_gen.unsafe_or_invalid", reason=reason, raw=resp.content[:200])
        return {**state, "error": f"Generated SQL is invalid: {reason}", "sql_query": ""}

    # Ensure LIMIT is present (guard against LLM skipping it)
    if "LIMIT" not in sql.upper():
        sql = sql.rstrip(";") + f" LIMIT {settings.max_rows_returned}"

    log.info("sql_gen.complete", sql_length=len(sql), explanation=meta.get("explanation", "")[:80])
    return {
        **state,
        "sql_query": sql,
        "sql_validated": True,
        "sql_explanation": meta.get("explanation", ""),
        "model_used": resp.model,
        "error": None,  # Clear any previous execution errors
    }
