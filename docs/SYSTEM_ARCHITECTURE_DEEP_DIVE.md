# Data Visualization Copilot — Complete System Architecture

> A comprehensive deep-dive into the AI-powered analytics platform. Every component, node, data flow, and design decision explained.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Backend Entry Point](#2-backend-entry-point--backendmainpy)
3. [Configuration](#3-configuration--backendconfigpy)
4. [The LangGraph Pipeline](#4-the-langgraph-pipeline--backendagentgraphpy)
5. [Pipeline Nodes (Deep Dive)](#5-pipeline-nodes-deep-dive)
6. [The Supervisor Meta-Node](#6-the-supervisor-meta-node)
7. [LLM Routing & Fallback](#7-llm-routing--fallback)
8. [Vector Memory (Qdrant)](#8-vector-memory-qdrant)
9. [Data Connectors](#9-data-connectors)
10. [ClickHouse Connector](#10-clickhouse-connector)
11. [DB Intelligence Layer](#11-db-intelligence-layer)
12. [Business RAG Layer](#12-business-rag-layer)
13. [LLM Cache (Canary Pattern)](#13-llm-cache-canary-pattern)
14. [Streaming (SSE) Pipeline](#14-streaming-sse-pipeline)
15. [Main Query Router](#15-main-query-router)
16. [Database Models](#16-database-models)
17. [Frontend Architecture](#17-frontend-architecture)
18. [End-to-End Request Flow](#18-end-to-end-request-flow)
19. [Security Architecture](#19-security-architecture)
20. [Caching Strategy](#20-caching-strategy)

---

## 1. Architecture Overview

```
┌─ Frontend (React 19 + Vite + TailwindCSS) ────────────────────────┐
│  CopilotPage.tsx → Zustand chat store → Axios API client          │
│  SSE streaming with real-time 7-step progress bar                 │
│  ECharts rendering via echarts-for-react                          │
│  Disambiguation modal, agent sidebar, session management           │
└──────────────────────────┬────────────────────────────────────────┘
                           │ HTTP REST + SSE (text/event-stream)
┌─ Backend (FastAPI + Uvicorn) ─────────────────────────────────────┐
│                                                                     │
│  main.py: creates FastAPI app, seeds demo data,                    │
│           registers datasources, starts background threads          │
│                                                                     │
│  Routers (9 mounted under /api/v1):                                │
│    auth, admin, analytics, knowledge, copilot (main query),        │
│    streaming (SSE), dashboards, reports, clickhouse                │
│                                                                     │
│  LangGraph Pipeline (14 nodes):                                    │
│    check_qa_memory → supervisor → {intent, schema, sql, exec,      │
│    analyze, viz, respond} — supervisor dynamically routes          │
│    after EVERY node completion                                     │
│                                                                     │
│  Caching (3 layers):                                               │
│    Qdrant vector memory (semantic cache)                           │
│    LLM Canary cache (15-min TTL, Redis + memory)                   │
│    QA memory (exact match)                                         │
│                                                                     │
│  Read-only enforcement (3 layers):                                 │
│    SQL validation in sql_gen → Connector enforcement → DB perms     │
│                                                                     │
│  Data connectors: SQLite, ClickHouse, PostgreSQL, CSV/DuckDB       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Backend Entry Point — `backend/main.py`

### App Creation

`create_app()` builds the FastAPI application:

```python
app = FastAPI(
    title="Data Visualization Copilot",
    description="AI-powered analytics: ask questions in plain English, get interactive charts",
    version="1.0.0",
)
```

### CORS

All origins allowed (`*`) — open for development. Hardened in production.

### Routers Mounted

| Router | Prefix | Purpose |
|--------|--------|---------|
| `auth` | `/api/v1` | JWT login, refresh, user CRUD, preferences |
| `admin` | `/api/v1` | Approval queue, trigger DB scan |
| `analytics` | `/api/v1` | Usage analytics, popular queries, intents |
| `knowledge` | `/api/v1` | Business glossary, DB knowledge CRUD, memory search |
| `copilot` | `/api/v1/copilot` | **Main POST /query**, upload, schema, history, datasource, disambiguate |
| `streaming` | `/api/v1/copilot` | SSE streaming (GET + POST /stream) |
| `dashboards` | `/api/v1/dashboards` | Saved dashboards with chart positions |
| `reports` | `/api/v1` | Report generation with schedules |
| `canary_compat` | `/api/v1` | Legacy compatibility endpoints |

### Startup Events (`@app.on_event("startup")`)

1. **`init_db()`** — Initializes SQLAlchemy async engine, creates tables
2. **Seed admin** — Creates `admin@demo.com` / `admin123` if users table empty
3. **Register datasources**:
   - `"default"` → SQLite at `./demo.db`
   - `"limese"` → ClickHouse at `118.95.209.221:8123` (credentials: `limese_interns` / `ItsInterns!23`)
4. **Seed demo data** — If sales table empty: 2000 sales rows, 500 users, 1000 support tickets
5. **Start DB Intelligence** — Background daemon thread deep-scans ClickHouse, caches to disk, auto-refreshes every 24h
6. **Mount frontend** — Serves static `index.html` for SPA routing (any non-API 404 serves `index.html`)

### Health Endpoint

```
GET /health → {"status": "healthy", "app": "Data Visualization Copilot", "version": "1.0.0"}
```

### Frontend Mounting

Detects built frontend in priority order: `backend/static` → `frontend/dist` → `static`. Mounts with `html=True` for SPA support. Custom exception handler returns `index.html` for any non-API 404 (SPA routing).

---

## 3. Configuration — `backend/config.py`

Uses `pydantic-settings` `BaseSettings` reading from `.env` at repo root.

### Key Configuration Groups

#### App
```python
app_name: str = "Data Visualization Copilot"
app_env: str = "development"
debug: bool = True
secret_key: str = "change-me-in-production-32chars-min"
```

#### JWT Auth
```python
jwt_secret_key: str = ""  # Falls back to secret_key
access_token_expire_minutes: int = 30
refresh_token_expire_days: int = 7
```

#### Database
```python
database_url: str = "sqlite+aiosqlite:///./dvc.db"  # SQLAlchemy async
```

#### LLM Providers
```python
# All optional — only used if valid key provided
groq_api_key: str = ""
anthropic_api_key: str = ""
openai_api_key: str = ""
gemini_api_key: str = ""
mistral_api_key: str = ""
openrouter_api_key: str = ""
deepseek_api_key: str = ""
cohere_api_key: str = ""
zhipu_api_key: str = ""  # Active in current deployment
```

#### Model Routing
```python
llm_fast_model: str = "zhipu/glm-5-turbo"      # Intent, supervisor, reviews
llm_smart_model: str = "zhipu/glm-5-turbo"      # SQL generation, analysis
llm_premium_model: str = "zhipu/glm-5-turbo"
llm_fallback_model: str = "zhipu/glm-5-turbo"
```

#### Vector Memory
```python
qdrant_url: str = "http://localhost:6333"
qdrant_collection: str = "dvc_memory"
qdrant_enabled: bool = True
```

#### Query Limits
```python
max_rows_returned: int = 10000
query_timeout_seconds: int = 90
max_conversation_history: int = 20
```

#### DB Scan
```python
db_scan_time: str = "02:00"  # 24h format, server local time
db_scan_deep_table_limit: int = 30
```

---

## 4. The LangGraph Pipeline — `backend/agent/graph.py`

Uses LangGraph's `StateGraph(AnalyticsState)` with a **meta-cognitive Supervisor** pattern. Unlike linear pipelines, every node returns to the Supervisor, which inspects the full state and dynamically decides the next step.

### Graph Visualization

```
check_qa_memory ──► supervisor ◄── (all nodes return here)
                          │
               ┌──────────┼──────────┐
               ▼          ▼          ▼
     understand_intent  route_tables  general_llm
               │          │
               ▼          ▼
          disambiguate  discover_schema
               │          │
               ▼          ▼
                    generate_sql
                         │
                    review_sql (DBA)
                         │
                    execute_sql
                         │
               ┌────────┼────────┐
               ▼        ▼        ▼
        analyze_insights  generate_viz_config
               │        │
               ▼        ▼
           review_insights (Critic)
                    │
               compose_response
                    │
                    ▼
                   END
```

### Node Registration

```python
graph.add_node("check_qa_memory", check_qa_memory)
graph.add_node("supervisor", supervisor)
graph.add_node("understand_intent", understand_intent)
graph.add_node("disambiguate", disambiguate)
graph.add_node("general_llm", handle_general_query)
graph.add_node("route_tables", route_tables)
graph.add_node("discover_schema", discover_schema)
graph.add_node("generate_sql", generate_sql)
graph.add_node("review_sql", review_sql)
graph.add_node("execute_sql", execute_sql)
graph.add_node("analyze_insights", analyze_insights)
graph.add_node("generate_viz_config", generate_viz_config)
graph.add_node("compose_response", compose_response)
graph.add_node("insight_followup", handle_insight_followup)
graph.add_node("review_insights", review_insights)
```

### Routing Logic

All nodes flow back to supervisor via `graph.add_edge(node, "supervisor")`. The supervisor calls `_route_next()` which reads `state["next_step"]`.

### `run_analytics_agent()` — Main Entry Point

```python
async def run_analytics_agent(question, datasource_id, session_id,
                               conversation_id, conversation_history, user_id):
```

1. Loads conversation history from MinIO if not provided
2. Compacts history via `compact_history()` (ReMe pattern — keeps context small)
3. Checks **Qdrant semantic cache** with validation:
   - Extracts years/months from both questions — rejects on mismatch
   - Rejects if one asks for trend/chart and the other doesn't
4. Builds initial `AnalyticsState`
5. Runs compiled graph via `graph.ainvoke(initial_state)`
6. Returns `final_response` dict with `total_latency_ms` and `model_used`

### Singleton Pattern

```python
_graph = None
def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
```

---

## 5. Pipeline Nodes (Deep Dive)

### The State Object — `AnalyticsState` (TypedDict, `state.py`)

94 lines. The single shared state flowing through all nodes:

```python
class AnalyticsState(TypedDict, total=False):
    # Input
    session_id: str
    conversation_id: str
    user_question: str
    datasource_id: str
    conversation_history: list[dict]
    user_id: str

    # Step 1: Intent
    intent: dict = {
        "type": "chart_request"|"data_query"|"follow_up"|"analytical_question"|"comparison"|"trend_analysis"|"export_request"|"greeting"|"conversational"|"off_topic",
        "chart_type_hint": "bar"|"line"|"pie"|"scatter"|"heatmap"|"gauge"|"table"|None,
        "time_range": "last_7_days"|"last_30_days"|"last_quarter"|"last_year"|"ytd"|"custom"|None,
        "aggregation": "sum"|"count"|"avg"|"max"|"min"|None,
        "entities": ["sales", "region"],
        "is_follow_up": bool,
        "confidence": float,
        "rephrased_question": str
    }

    # Step 2: Schema
    schema_context: dict = {"relevant_tables": [...], "suggested_joins": [...], "all_tables": [...]}

    # Step 3: SQL
    sql_query: str
    sql_validated: bool
    sql_explanation: str

    # Step 4: Query Results
    query_results: dict = {"columns": [...], "rows": [...], "row_count": int, "execution_time_ms": int}

    # Step 5: Insights
    insights: list[str]
    key_metrics: dict
    anomalies: list[str]

    # Step 6: Visualization
    viz_config: dict  # ECharts JSON
    viz_type: str     # bar|line|pie|scatter|heatmap|gauge|table

    # Step 7: Response
    response_text: str
    follow_up_questions: list[str]
    final_response: dict

    # Routing & Metadata
    skip_pipeline: bool
    pre_filter_response: dict
    error: str | None
    step_errors: list[str]
    next_step: str
    supervisor_thoughts: list[str]
    insights_validated: bool
    critic_feedback: str
    sql_retry_count: int
    review_retry_count: int
    critic_retry_count: int
```

---

### Node 0: `check_qa_memory` (`nodes/cache_check.py`, 61 lines)

Entry point cache check.

**Two-tier matching:**
- **High similarity (≥0.92)** → Returns full cached answer, sets `skip_pipeline=True`, goes directly to `compose_response`
- **Medium similarity (≥0.75)** → Reuses cached SQL but still validates and executes (fresh data)

Only checks for non-follow-up questions.

---

### Node 1: `understand_intent` (`nodes/intent.py`, 140 lines)

Two-step classification:

**Step A — Rule-based Pre-filter** (`pre_filter.py`, 154 lines):

Runs BEFORE any LLM call to save tokens:

```python
def pre_classify(question: str) -> dict:
    # Empty check → "empty" type
    # Greeting regex: ^(hi|hello|hey|gm|gn|good morning|...)$
    #   → "greeting" type with rich varied responses (multiple variants per greeting)
    # Everything else → "llm_classify" type (go to LLM)
```

Greeting responses are hand-crafted with random selection per greeting type. Example:
```python
greetings = {
    "hi": [
        "# 👋 Hi there! I'm your Data Analytics Copilot\n\nI'm here to help you...",
        "# Hey! 👋 Great to see you!\n\nReady to dive into some data?...",
    ],
    "how are you": [
        "# I'm doing great, thank you! 🤖\n\nI'm fully operational...",
    ],
}
```

Off-topic detection is disabled — delegated to LLM for dynamic handling.

**Step B — LLM Classification** (fast 8B model):

Prompt includes:
- Last 4 messages of conversation history (for follow-up context)
- Question text
- Classification rules for 11 intent types
- Chart type selection rules (trend→line, ranking→bar, proportions→pie, etc.)
- Rephrasing instructions for follow-ups

Output JSON:
```json
{
  "type": "chart_request",
  "chart_type_hint": "bar",
  "time_range": "last_year",
  "aggregation": "sum",
  "entities": ["sales", "platform"],
  "filters": {"platform": "Nykaa"},
  "is_follow_up": false,
  "needs_comparison": false,
  "confidence": 0.95,
  "rephrased_question": "Show total revenue by platform for 2025"
}
```

If LLM fails JSON parse → falls back to `data_query` with 0.5 confidence. For follow-ups with unchanged rephrased question, enriches with previous assistant context.

---

### Node 2.0: `route_tables` (`nodes/route_tables.py`, 66 lines)

Runs in parallel with `understand_intent`. Semantic table routing:

```python
async def route_tables(state):
    # 1. Fetch user history profile
    history_profile = get_user_profile(user_id, session_id)

    # 2. Score all 173 tables semantically
    ranked_tables = score_tables(question, user_id, history_profile)

    # 3. Compute ambiguity score
    # Competing domains with close scores + vague terms → high ambiguity
```

**Ambiguity detection:**
- Top 2 tables from different domains
- Score difference < 3.0
- Question contains vague terms (sales, revenue, data, report...)
- → `ambiguity_score = 0.8`

If clear → pre-populates `candidate_tables` for schema discovery. If `locked_tables` already set (from disambiguation) → skips entirely.

---

### Node 2.1: `disambiguate` (`nodes/disambiguate.py`, 99 lines)

Only triggered when `ambiguity_score > 0.6`.

**Flow:**
1. Supervisor detects high ambiguity → routes to `disambiguate`
2. Disambiguate node sends `DISAMBIGUATION_NEEDED:` error prefix to frontend
3. Frontend shows `DisambiguationModal` with competing table options
4. User picks → `locked_tables` set in state
5. Subsequent calls skip routing and go directly to `discover_schema`

---

### Node 2.2: `discover_schema` (`nodes/schema.py`, 191 lines)

**Keyword → table mapping** for instant fallback:

```python
_TABLE_KEYWORDS = {
    "combined_sales_final": ["revenue", "sales", "order", "platform", ...],
    "product_master": ["product", "item", "sku", "category", "brand", ...],
    "inventory_sales_overview_new": ["inventory", "stock", "warehouse", ...],
    "shopify_orders": ["shopify", "online store", "website"],
    "unicomm_sales_final": ["unicomm", "unicommerce"],
    ...
}
```

**Scoring algorithm** (`_keyword_select_tables`):

| Rule | Points | Example |
|------|--------|---------|
| Exact table name match | +20 | "inventory" → inventory_sales_overview_new |
| Partial table name match | +15 | "shop" → shopify_orders (+15 from partial "ship") |
| Platform keyword match | +18 | "Nykaa" → platform_sku_mapping |
| General keyword match | +10 | "revenue" → combined_sales_final |
| Column name match | +5 | "item_name" in question |
| Column partial match | +3 | "name" in question |
| Description match | +2 | per word in description |

Returns top 4 tables with full column metadata, sample data, row counts. Records table access in user history.

---

### Node 3: `generate_sql` (`nodes/sql_gen.py`, 513 lines)

The most complex node — generates ClickHouse SQL with multi-layered context assembly.

**Context Assembly (in order):**

1. **Dynamic Schema Expansion** (`DynamicSchemaAgent`):
   - Scans question for missing table keywords (0ms overhead)
   - Expands table context if new tables found

2. **DB Intelligence Context** (for ClickHouse):
   - Loads cached context from `db_intelligence.json`
   - `build_sql_context_prompt()` generates compact LLM prompt:
     - Only relevant tables (from discover_schema)
     - Only useful columns (sorted: annotated first, categorical first)
     - Capped categorical values (max 5)
     - Global notes (business facts)

3. **Business RAG Context**:
   - `build_rag_prompt()` keyword-matches glossary, metrics, platforms, QA
   - Injected directly into prompt

4. **Few-shot Examples** from Qdrant:
   - `search_similar_queries()` with threshold 0.85
   - **Smart filtering**: If user didn't ask for units, filter out examples selecting units. Same for revenue.

5. **14 ClickHouse-Specific Rules** (hardcoded in prompt):

```
1. Table aliases MUST be declared: FROM combined_sales_final csf
2. Window functions: use lagInFrame() / leadInFrame() — NOT lag() / lead()
3. Date filtering: date_created >= '2025-01-01' (string comparison)
4. Date grouping: formatDateTime(date_created, '%Y-%m') AS month
5. Always filter: WHERE final_status NOT IN ('cancelled', ... ,'Returned')
6. Revenue column: row_subtotal (NOT order_price)
7. Units column: quantity_ordered (NOT shipped_qty — always 0)
8. JOIN pattern: csf LEFT JOIN product_master pm ON csf.internal_sku = pm.internal_sku
9. Category filter: pm.category_l1 IN ('Skincare', 'Makeup', 'Haircare')
10. Always end with LIMIT
11. STRICT DATE PERIOD FILTERING for specific years
12. SINGLE-MONTH TREND → group by day for daily trend line
13. ClickHouse GROUP BY safety: every non-metric SELECT col must be in GROUP BY
14. Shopify query routing: use shopify_orders for Shopify-specific queries
```

6. **Column Selection Warnings**:
   - User asks only for revenue → warning: DO NOT select quantity_ordered
   - User asks only for units → warning: DO NOT select row_subtotal

7. **Date Regex Parsing**:
   - Extracts years (20[12]\d) from question
   - Extracts month names (january, feb...)
   - Extracts digit months (MM-YYYY, YYYY-MM)
   - Generates strict date filter warnings

8. **Retry Context** (if regenerating after DBA failure):
   - Shows failed SQL + error message
   - `DynamicSchemaAgent.resolve_execution_error()` provides corrective advice

**Read-Only Security (5 layers):**

```python
FORBIDDEN_PATTERNS = [
    r"\bDROP\b", r"\bDELETE\b", r"\bTRUNCATE\b", r"\bALTER\b",
    r"\bCREATE\s+(TABLE|INDEX|VIEW|DATABASE|SCHEMA)\b",
    r"\bINSERT\b", r"\bUPDATE\b", r"\bREPLACE\b", r"\bMERGE\b",
    r"\bGRANT\b", r"\bREVOKE\b", r"\bSET\s+ROLE\b",
    r"\bEXEC\b", r"\bEXECUTE\b",
    r"\bCOMMIT\b", r"\bROLLBACK\b", r"\bBEGIN\b",
    r";--", r";#", r"\/\*.*\*\/;.*SELECT", r"--.*;.*DROP",
    r";.*\b(SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE)\b",
]
```

5 validation layers:
1. Must start with SELECT
2. No forbidden patterns
3. No multi-statement (semicolons + keywords)
4. No comment injection
5. No dangerous functions (system, exec, eval)

**SQL Parsing** (`_parse_sql_from_llm`):
1. Try JSON parse (strip markdown fences first)
2. Brace-counting parser for nested JSON
3. Fallback: raw SELECT...LIMIT regex
4. Clean trailing JSON artifacts

---

### Node 3.5: `review_sql` (`nodes/sql_reviewer.py`, 164 lines) — DBA Review

Two-layer validation:

**Layer 1 — Fast Regex Pre-check** (Python-side, 0ms):
- Calls `_is_safe_sql()` (5 security layers)
- For ClickHouse:
  - Cancelled/returned exclusion check
  - Revenue column: `row_subtotal` not `order_price`
  - Window functions: `lagInFrame` not `lag`
  - Table alias presence

**Layer 2 — LLM Deep Review** (fast 8B model):
- 8 business rules checked
- Semantic review against user intent
- Column selection verification

**Retry logic:** If fails → `sql_validated=False` + `dba_feedback` → supervisor routes back to `generate_sql`. Max 2 review retries.

---

### Node 4: `execute_sql` (`nodes/executor.py`, 157 lines)

**Execution:**
```python
result = await execute_query(datasource_id, sql, timeout=settings.query_timeout_seconds)
```
Returns `{columns, rows, row_count, execution_time_ms, truncated}`

**Auto-fix on ClickHouse errors:**
If error contains fixable patterns (`unknown_identifier`, `missing columns`, `no such column`, `unknown function`, `syntax error`):
1. Calls LLM with broken SQL + error + schema context
2. LLM returns corrected SQL
3. Retries execution once
4. Logs the fix description

**Retry:** Increments `sql_retry_count`. Max 2 retries.

---

### Node 5: `analyze_insights` (`nodes/analyst.py`, 277 lines)

**Step 1 — Client-side Stats** (0 LLM tokens):
```python
def _compute_basic_stats(rows, columns):
    # Identifies numeric vs string columns
    # Skips ID/SKU columns (internal_sku, external_sku, product_id, etc.)
    # Computes min, max, avg, total for each numeric column
```

Non-metric columns auto-skipped: `internal_sku`, `external_sku`, `sku`, `product_id`, `order_id`, `id`, `customer_id`, `user_id`, `platform_id`, `category_id`, `brand_id`, `pincode`, etc.

**Step 2 — LLM Insight Generation** (smart model):
- Takes stats summary + sample data (10 rows) + total row count
- Returns JSON: `insights[]`, `key_metrics{}`, `anomalies[]`, `trend`, `top_performer`, `bottom_performer`, `summary_sentence`
- Uses ₹ (Rupee) for monetary values, Indian formatting (Cr, L, K)
- If critic feedback exists → warning to fix errors

**Critic notes:**
```python
insights_rules = """
- DO NOT just read out numbers. That is useless.
- Analyze the trend: peaks, troughs, overall direction, significant changes.
- If user asks for "reasons" or "causes", EVERY insight must be a concrete reason.
- Use ₹ only for monetary values (Revenue, Sales, Spend, Profit).
- Format large numbers in Indian style: ₹23.8 Cr, 2.8 L units.
"""
```

**Rule-Based Fallback** (if LLM fails):
- Peak finding: highest value entity
- Growth rate: start vs end of period
- Category comparison: best vs second-best
- Always: row count summary

---

### Node 5.5: `review_insights` (`nodes/critic.py`, 122 lines) — Critic Agent

**Critic validation:**
1. Takes raw query results (50 rows) + generated insights
2. LLM auditor checks:
   - **Mathematical accuracy**: verify every number against raw data
   - **Truthfulness**: no hallucinations
   - **Contextual scope**: timeframe matches query
   - **Empty data**: no invented trends
3. If passes → `insights_validated=True`
4. If fails → `insights_validated=False` + `critic_feedback` → supervisor routes back to `analyze_insights` with regeneration
5. Max 2 critic retry loops
6. If LLM fails → passes by default (graceful degradation)

---

### Node 6: `generate_viz_config` (`nodes/viz_config.py`, 635 lines)

Generates complete Apache ECharts option objects. **9 chart types**:

| Type | ECharts Series | When Used |
|------|---------------|-----------|
| bar | `bar` | Ranking/comparing categories |
| line | `line` | Time series / trends |
| area | `line` with `areaStyle` | Cumulative trends |
| pie | `pie` | Proportions/shares |
| scatter | `scatter` | Correlations |
| heatmap | `heatmap` | 2D distributions |
| gauge | `gauge` | Single KPI value |
| funnel | `funnel` | Conversion stages |
| table | Custom render | Raw data display |

**Smart Column Filtering:**
```python
REVENUE_WORDS = {"revenue", "sales", "earning", "income", "amount", "price", "spend", "value", "subtotal", "profit"}
UNIT_WORDS = {"unit", "qty", "quantity", "volume", "order", "count", "sold"}

# If user asked only for revenue → drop unit columns from chart
# If user asked only for units → drop revenue columns from chart
```

**Pivoting Logic** (`_pivot_and_build_multi_series`):
1. Classify columns as numeric vs string (checks first 20 rows)
2. If 1 string + 1+ numeric columns → simple X-Y chart
3. If 2+ strings + 1+ numeric → pivot: unique values of second string column become series
4. Heatmap for 2 string + 1 numeric (three-column result)
5. Gauge for 1 numeric + no string columns
6. Dual Y-axis when scales of numeric columns differ significantly

**Color Palette:**
```python
COLORS = ["#5470c6", "#91cc75", "#fac858", "#ee6666", "#73c0de",
          "#3ba272", "#fc8452", "#9a60b4", "#ea7ccc"]
```

---

### Node 7: `compose_response` (`nodes/responder.py`, 691 lines)

Three parallel generation paths:

**1. Executive Summary** — LLM generates narrative text:
- Incorporates chart description
- Highlights key metrics
- Contextualizes findings with business context

**2. Follow-up Questions** — LLM generates 3 context-aware suggestions:
- Related to current query
- Natural next questions a business analyst would ask

**3. Storage** (all async):
- **Qdrant**: stores question + SQL + results for semantic cache
- **QA memory**: stores question + answer for exact-match cache
- **MinIO**: stores conversation history (backup)
- **SQLite**: stores message in messages table

**Response modes:**
- **Greeting** → Returns pre-filter response directly (skip LLM)
- **Conversational/off-topic** → From general_llm node response
- **Analytical question** → Narrative answer with data citations
- **Chart request** → Chart config + summary + insights
- **Data query** → Table + row count + key metrics
- **Comparison** → Merges web search + internal analytics
- **Error** → Friendly error message + alternative suggestions

**Follow-up generation prompt:**
```
The user asked: "{question}"
We answered with: "{response_text}"

Generate 3 natural follow-up questions a business analyst or manager 
would logically ask next. Make them specific and actionable.
Return JSON: {"follow_ups": ["...", "...", "..."]}
```

---

## 6. The Supervisor Meta-Node

**`nodes/supervisor.py`** (157 lines) — The brain of the system.

After EVERY node completes:
1. Inspects full state (intent, schema, SQL, results, insights, viz, errors, retry counts)
2. Calls **fast LLM** (8B model) with 13 routing rules
3. Reads `next_step` from LLM response
4. If LLM fails → **static fallback rules** prevent pipeline from hanging

### LLM Decision Prompt

```python
prompt = f"""Current Execution State:
- Intent Type: "{intent_type}"
- Ambiguity Score: {ambiguity_score}
- Schema Context: {"Loaded" if schema_context else "Empty"}
- SQL Query: {"Generated" if sql_query else "None"}
- SQL Validated: {sql_validated}
- Query Results: {row_count} rows
- Insights: {len(insights)} items
- Insights Validated: {insights_validated}
- Viz Ready: {bool(viz_config)}
- Error: "{error}"
- Retries: SQL={sql_retry}/2, DBA={review_retry}/2, Critic={critic_retry}/2

DECISION RULES:
1. Empty intent → "understand_intent"
2. Greeting/conversational → "general_llm"
3. Ambiguity > 0.6 → "disambiguate"
4. Data query, no schema → "discover_schema"
5. Schema, no SQL → "generate_sql"
6. SQL not validated, no exec error → "review_sql"
7. SQL not validated, DBA feedback → "generate_sql" (fix)
8. SQL validated, no results, no error → "execute_sql"
9. SQL exec failed, retries < 2 → "generate_sql" (fix)
10. Results, no insights → "analyze_insights"
11. Results, no viz → "generate_viz_config"
12. Insights > 0, not validated, no critic_feedback → "review_insights"
13. Insights > 0, not validated, critic feedback, retries < 2 → "analyze_insights"
14. All done → "compose_response"
15. Already responded → "__end__"
"""
```

### Static Fallback Rules

If LLM fails to parse, the system falls back to deterministic rules ensuring it never hangs:

```python
if not intent_type: next_step = "understand_intent"
elif intent_type in ("greeting", "conversational", "off_topic"): next_step = "general_llm"
elif not schema_context: next_step = "discover_schema"
elif not sql_query: next_step = "generate_sql"
elif not sql_validated: next_step = "review_sql" if review_retry < 2 else "execute_sql"
elif not query_results:
    if error and sql_retry < 2: next_step = "generate_sql"
    elif not error: next_step = "execute_sql"
    else: next_step = "compose_response"
elif not viz_config: next_step = "generate_viz_config"
elif not insights: next_step = "analyze_insights"
elif not insights_validated and critic_retry < 2:
    next_step = "review_insights" if not critic_feedback else "analyze_insights"
else: next_step = "compose_response"
```

---

## 7. LLM Routing & Fallback

**`backend/agent/llm.py`** (238 lines) — LiteLLM wrapper with multi-model fallback.

### Model Assignment by Task

| Task | Primary Model | Fallback Chain |
|------|-------------|----------------|
| **routing** | `llm_fast_model` | Zhipu GLM-5 → OpenRouter Gemini Flash → Gemini Flash → Groq 8B → DeepSeek → Mistral |
| **sql** | `llm_smart_model` | Zhipu GLM-5 → OpenRouter Llama 70B → OpenRouter Gemini Flash → Gemini Flash → Gemini Pro → Groq 70B → Groq 8B → DeepSeek → Mistral |
| **analysis** | Same as sql | Same as sql |
| **general** | Same as sql | Same as sql |

### Key Behavior

```python
temperature = 0.0  # Always forced — deterministic responses
```

**Task-specific timeouts:**
```python
task_timeouts = {
    "routing": 30.0,
    "sql": 45.0,
    "analysis": 45.0,
    "general": 45.0,
}
```

**Zhipu/GLM handling:**
```python
if "zhipu" in m or "glm" in m:
    actual_model = m.split("/", 1)[-1]  # strip "zhipu/" prefix
    kwargs = {
        "model": f"openai/{actual_model}",  # OpenAI-compatible endpoint
        "api_key": settings.zhipu_api_key,
        "api_base": settings.zhipu_base_url,  # https://api.z.ai/api/coding/paas/v4
    }
```

**Fallback chain building** (`_build_fallback_chain`):
- Only includes models with valid API keys (rejects empty, placeholder, asterisk keys)
- Order: Zhipu → OpenRouter → Gemini → Groq → DeepSeek → Mistral
- Routing tasks skip 70B models (too slow for routing)

**Rate limit handling:**
```python
if "rate_limit" in err_str or "429" in err_str:
    if attempt == 0:
        await asyncio.sleep(0.5)
        continue  # retry same model once
```

**Error handling by task:**
- `task == "sql"` → Raises `RuntimeError` on total failure (must succeed)
- All other tasks → Returns empty stub `LLMResponse(content="")` so pipeline doesn't crash

### API Key Validation

```python
def _is_valid_key(key: str | None) -> bool:
    if not key: return False
    key_strip = key.strip()
    if not key_strip or "*" in key_strip: return False
    if "placeholder" in key_strip.lower(): return False
    if "your_" in key_strip.lower(): return False
    return True
```

---

## 8. Vector Memory (Qdrant)

**`backend/agent/memory.py`** (131 lines)

### Setup
- **Embedding model**: `BAAI/bge-base-en-v1.5` (768-dimension vectors)
- **Distance**: COSINE
- **Collection**: `dvc_memory`
- **Enabled**: Controlled by `settings.qdrant_enabled`

### Key Methods

```python
def connect(self):
    # Lazy connection — only connects on first use
    # If fails → client = None (retries on next call — handles cold-boot races)

def embed_text(self, text: str) -> list[float]:
    # Single text → 768-dim vector

def store_query(self, user_id, question, sql, payload):
    # Upserts into Qdrant
    # ID: hash(question) % ((1<<63)-1)

def search_similar_queries(self, question, user_id="anonymous", limit=3) -> list[dict]:
    # Returns top-3 similar questions
    # Filters by user_id
    # Score threshold: 0.85

def search_semantic_cache(self, question, user_id="anonymous", threshold=0.92) -> dict | None:
    # Returns single best match
    # High threshold (0.92) for exact cache hits
    # Filters by user_id
```

### Connection Resilience

```python
try:
    self.client = QdrantClient(url=settings.qdrant_url, timeout=5)
except Exception:
    # Do NOT permanently disable — reset client so next call retries.
    # This handles cold-boot race conditions where Qdrant starts after the server.
    self.client = None
    self.embedding_model = None
```

---

## 9. Data Connectors

**`backend/data/connector.py`** (496 lines)

### Supported Datasources

| Type | Backend | Schema Introspection |
|------|---------|---------------------|
| `sqlite` | `asyncio.get_event_loop().run_in_executor()` (thread pool) | `PRAGMA table_info` + row count + 2 sample rows |
| `clickhouse` | `clickhouse_connect` (thread pool) | Delegates to `ClickHouseConnector` + DB Intelligence |
| `postgresql` | `asyncpg` | `information_schema` queries |
| `csv` | DuckDB (`read_csv_auto`) | DuckDB schema inference |

### Schema Cache

```python
# 1-hour in-memory TTL per datasource
# Disk cache at backend/data/schema_cache.json
# Auto-saves on refresh
```

### Security — Layer 2 Enforcement

```python
async def _is_readonly_query(sql: str) -> bool:
    # Must start with SELECT or WITH
    # Scan for modifying keywords
    # Multi-statement detection
    # Comment injection blocking
    # PermissionError if violated (logged)
```

---

## 10. ClickHouse Connector

**`backend/data/clickhouse_connector.py`** (307 lines)

### Client Caching

```python
_client_cache: dict[tuple, Client] = {}
# Key: (host, port, username, password, database)
# Reuses client for multiple queries
```

### Execution

```python
def execute(self, sql: str) -> dict:
    # Runs in thread pool executor
    # Returns {columns, rows, row_count}
    # Converts ISO format dates (datetime → str)
    # Preserves SKU/ID columns as strings (prevents leading-zero loss)
```

### Schema Introspection

Two paths:
1. **Fast path**: Loads 12 priority tables from `db_intelligence.json` (already cached on disk)
2. **Fallback**: Live `DESCRIBE TABLE` + row count + 2 sample rows

### Hardcoded Table Descriptions

```python
TABLE_DESCRIPTIONS = {
    "combined_sales_final": "Main sales table — one row per line item per order. (~340K rows) ...",
    "product_master": "Product catalog with categories, MRP, COGS ...",
    "inventory_sales_overview_new": "Daily inventory snapshots ...",
    "shopify_orders": "Shopify storefront orders — separate schema from combined_sales_final ...",
    ...
}
```

Each description includes exact column names, platform names, revenue calculation rules, join patterns, and business context.

---

## 11. DB Intelligence Layer

**`backend/services/db_intelligence.py`** (684 lines)

### Purpose

Deep-scans the Limese ClickHouse database and builds a comprehensive context document injected into every LLM SQL-generation prompt. Runs as a **background daemon thread** with 24-hour auto-refresh.

### Priority Tables (Deep Scan)

```
combined_sales_final, product_master, product_catlog,
inventory_sales_overview_new, platform_sku_mapping,
shopify_orders, unicomm_sales_final, zoho_sales_final,
zoho_purchase_orders, inventory_ledger, product_hierarchy, lead_time
```

### What It Extracts Per Table

- Row count & date range
- Every column: type, unique count, exact categorical values (≤200 unique)
- Numerical ranges (min, max, mean)
- Business facts (total revenue, orders, units, date coverage)
- Column-level annotations

### Column Annotations

Hardcoded in `db_intelligence.py:57-100` — critical facts LLM MUST know:

```python
COLUMN_ANNOTATIONS = {
    "combined_sales_final": {
        "sales_platform": "DIMENSION — use to GROUP BY platform. Contains exact platform names.",
        "client_name": "CONSTANT 'Limese' for all rows — NEVER group by this.",
        "row_subtotal": "REVENUE per line item — USE THIS for revenue/sales.",
        "quantity_ordered": "UNITS per line — USE THIS for unit counts.",
        "date_created": "Primary date column. Filter: date_created >= '2025-01-01'.",
        "final_status": "Order outcome. ALWAYS exclude cancelled/returned.",
        "internal_sku": "Join key → product_master.internal_sku.",
    },
    "product_master": {
        "internal_sku": "Primary key. Join with combined_sales_final.internal_sku.",
        "item_name": "Product display name.",
        "category_l1": "Top-level category. Values: Skincare, Makeup, Haircare.",
        "mrp": "Maximum Retail Price.",
        "cogs": "Cost of Goods Sold — use for margin: mrp - cogs.",
    },
    "inventory_sales_overview_new": {
        "sku": "Internal SKU — join to product_master.internal_sku.",
        "date": "Snapshot date. For latest stock: WHERE date >= today() - 2",
        "inventory": "Units on hand RIGHT NOW. USE THIS for stock level queries.",
        "order_quantity": "Units sold that day.",
        "gross_sales_rs": "Daily revenue in ₹.",
    },
    ...
}
```

### SQL Context Prompt Builder

```python
def build_sql_context_prompt(ctx, question, relevant_table_names):
    # Only include relevant tables
    # Sort annotated + categorical columns first
    # Cap categorical values at 5
    # Build "Global Notes" section with derived rules
    # Dynamically scan missing tables on-the-fly
```

### Caching

- **In-memory**: 24-hour TTL
- **Disk**: `backend/data/db_intelligence.json`
- **Stale-while-revalidate**: Clean boot returns minimal context (<0.2s), then deep-scans in background
- **Background refresh**: Daemon thread, initial scan after 5s, then every 24h

---

## 12. Business RAG Layer

**`backend/services/business_rag.py`** (197 lines)

Domain knowledge for Limese beauty brand. Four categories:

### Business Glossary (15 terms)
```
Nykaa Beauty, Myntra_PPMP, Shopify, Unicomm, Zoho,
GMV, D2C, MoM, PPMP, row_subtotal, final_status,
SKU, Limese, inventory, category_l1, category_l2
```

### Metric Definitions (7 metrics)
```
revenue: row_subtotal from orders where final_status NOT cancelled/returned
orders: Count of orders with valid order_id
aov: Average Order Value = Total Revenue / Number of Orders
inventory value: Current stock quantity * MRP
return rate: Percentage of orders marked as 'returned'
cancellation rate: Percentage of orders marked as 'cancelled'
```

### Common Q&A (4 pairs)
```
Q: What platforms does Limese sell on?
A: Nykaa Beauty, Myntra, Shopify (D2C), and through B2B partners like Unicomm.

Q: How is revenue calculated?
A: SUM(row_subtotal) where final_status NOT cancelled or returned.

Q: What is the difference between Nykaa and Myntra?
A: Nykaa is beauty-focused, Myntra is fashion-focused.

Q: What is category_l1 vs category_l2?
A: L1 is top-level (Skincare, Makeup, Haircare), L2 is specific sub-category.
```

### Platform Insights (4 platforms)
```
Nykaa: Beauty marketplace, high volume, competitive commission structure
Myntra: Fashion marketplace, seasonal trends important
Shopify: D2C channel, higher margins, direct customer relationship
Unicomm: B2B distribution, bulk orders, different pricing model
```

### `build_rag_prompt()` — Keyword matching query against all four categories, injects into LLM prompts.

### File-based Cache

```
/tmp/dvc_metadata/business_rag_cache.json
```

Also supports `add_custom_entry()` and `get_all_knowledge()` for CRUD via knowledge management API.

---

## 13. LLM Cache (Canary Pattern)

**`backend/services/llm_cache.py`** (156 lines)

### Design
- **15-minute TTL** (short window — "canary" pattern)
- **Max 200 entries** (LRU eviction)
- **Dual storage**: Thread-safe in-memory dict + Redis
- **Key**: `SHA256(datasource_id:normalized_question)`

### Key Operations

```python
def get_async(self, datasource_id: str, question: str) -> dict | None:
    # Check Redis first, fall back to memory
    # Reject results with 0 rows (forces fresh agent run)

def set_async(self, datasource_id: str, question: str, results: dict):
    # Write to both memory and Redis

def invalidate(self, datasource_id: str, question: str):
    # Remove from both caches

def stats(self):
    # Track hit/miss counts
```

---

## 14. Streaming (SSE) Pipeline

### `backend/routers/streaming.py` (398 lines)

Two endpoints:
```python
GET  /api/v1/copilot/stream?question=...&datasource_id=...
POST /api/v1/copilot/stream  # JSON body
```

Both call `_stream_agent_execution()`:
1. Creates/loads conversation from DB
2. Loads last 20 messages of history
3. Saves user message
4. Disambiguation check (same as POST /query)
5. QA memory cache check (≥0.92 similarity → cached)
6. LLM cache check (15-min Canary)
7. Runs `StreamingGraphRunner.astream()`
8. On `complete`: saves assistant message to DB, caches result

SSE headers:
```
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

### `backend/agent/streaming_graph.py` (395 lines)

`StreamingGraphRunner` wraps every node with a progress-tracking callback.

```python
STEPS = {
    "supervisor":           {"progress": 5,  "message": "Supervisor planning reasoning loop..."},
    "understand_intent":    {"progress": 10, "message": "Understanding your question..."},
    "route_tables":         {"progress": 15, "message": "Selecting target tables..."},
    "disambiguate":         {"progress": 20, "message": "Clarifying ambiguous terms..."},
    "general_llm":          {"progress": 50, "message": "Generating response..."},
    "discover_schema":      {"progress": 25, "message": "Exploring database structure..."},
    "generate_sql":         {"progress": 40, "message": "Writing SQL query..."},
    "review_sql":           {"progress": 50, "message": "DBA reviewing SQL query..."},
    "execute_sql":          {"progress": 65, "message": "Running query on database..."},
    "analyze_insights":     {"progress": 75, "message": "Analyzing results..."},
    "review_insights":      {"progress": 82, "message": "Validating insights correctness..."},
    "generate_viz_config":  {"progress": 88, "message": "Creating visualization..."},
    "compose_response":     {"progress": 95, "message": "Preparing response..."},
}
```

**`_wrap_node()` decorator:**
- Emits `{type: "progress", step, progress, message, data}` before and after each node
- Each node adds specific partial data:
  - `understand_intent` → intent type, rephrased question
  - `discover_schema` → tables selected
  - `generate_sql` → SQL text
  - `supervisor` → next step, thoughts
  - `review_sql` → validated flag, feedback
  - `execute_sql` → row count, columns
  - `analyze_insights` → insights, key metrics
  - `review_insights` → validated flag, critic feedback
  - `generate_viz_config` → viz type

**`astream()` yields events:**
```python
yield {"type": "progress", "step": "generate_sql", "progress": 40, "message": "...", "data": {...}}
yield {"type": "complete", "result": {...}, "total_latency_ms": 12345}
yield {"type": "error", "error": "...", "traceback": "...", "step": "..."}
```

---

## 15. Main Query Router

**`backend/routers/copilot.py`** (615 lines)

### `POST /api/v1/copilot/query`

Request body:
```json
{
  "question": "Show revenue by platform for 2025",
  "datasource_id": "limese",
  "conversation_id": null
}
```

Response:
```json
{
  "conversation_id": "uuid",
  "message_id": "uuid",
  "text": "...",
  "chart": {"type": "bar", ...},
  "insights": ["Nykaa leads with ₹200 Cr..."],
  "key_metrics": {"total_revenue": "₹563.95 Cr"},
  "follow_up_questions": ["Show monthly trend..."],
  "sql": "SELECT ...",
  "sql_explanation": "This query aggregates revenue by platform...",
  "row_count": 4,
  "viz_type": "bar",
  "columns": ["platform", "revenue"],
  "rows": [{"platform": "Nykaa", "revenue": 200000000}],
  "total_latency_ms": 12345,
  "model_used": "zhipu/glm-5-turbo",
  "error": null
}
```

**Endpoint flow:**
1. **Security check** — Blocks dangerous keywords in the input question itself (before even reaching the agent)
2. **Disambiguation check** — Calls `check_disambiguation()` for ambiguous terms
3. **LLM cache check** — Canary pattern, 15-min TTL
4. **Agent execution** — `run_analytics_agent()` with full pipeline
5. **Query logging** — Logs to `QueryLog` table (intent, latency, model, cache hit)
6. **Persistence** — Saves user + assistant messages to DB
7. **Caching** — Caches successful results (row_count > 0) in LLM cache
8. **Role-based filtering** — Hides SQL for non-business_analyst users

### Other Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/profile` | GET | Runs full pipeline with test question, returns per-node timing |
| `/debug` | GET | Streaming graph diagnostics |
| `/upload` | POST | CSV/Excel file upload → creates datasource |
| `/schema/{datasource_id}` | GET | Datasource schema |
| `/datasources` | GET | Registered datasources list (passwords masked) |
| `/history/{conversation_id}` | GET | Full conversation with messages |
| `/datasource` | POST | Register new datasource |
| `/disambiguate` | POST | Re-run query with resolved term |

---

## 16. Database Models

**8 SQLAlchemy models** in `backend/models/`:

### `User` (`user.py`)
```python
class User(Base):
    __tablename__ = "users"
    id, email, name, hashed_password, role, is_active, last_login, preferences
    Methods: hash_password(), verify_password(), can_view_sql(), can_export_data(), can_modify_documents()
    Roles: admin, business_analyst, non_tech_user, team_member
```

### `Conversation` + `Message` (`conversation.py`)
```python
class Conversation(Base):
    __tablename__ = "conversations"
    id, user_id (FK), datasource_id, title, created_at, updated_at

class Message(Base):
    __tablename__ = "messages"
    id, conversation_id (FK), role, content, sql_query, query_results (JSON),
    viz_config (JSON), insights (JSON), follow_up_questions (JSON),
    intent (JSON), model_used, latency_ms, tokens_used, error
```

### `Datasource` (`datasource.py`)
```python
class Datasource(Base):
    __tablename__ = "datasources"
    id, name, type (postgresql/clickhouse/csv/excel/sqlite), owner_id (FK),
    connection_config (JSON), schema_cache (JSON), is_active, row_count
```

### `Dashboard` (`dashboard.py`)
```python
class Dashboard(Base):
    __tablename__ = "dashboards"
    id, name, layout (JSON), is_public, refresh_interval_seconds
    # Has related DashboardChart model
```

### `Report` (`report.py`)
```python
class Report(Base):
    __tablename__ = "reports"
    id, name, owner_id (FK), conversation_id (FK), content (markdown),
    charts (JSON), schedule (JSON - cron), recipients (JSON), format (pdf/excel/html)
```

### `QueryLog` (`query_log.py`)
```python
class QueryLog(Base):
    __tablename__ = "query_log"
    id, user_id, conversation_id, datasource_id, question, intent_type,
    sql_query, row_count, viz_type, latency_ms, cache_hit, model_used, error, success
```

### `ApprovalQueue` (`approval_queue.py`)
```python
class ApprovalQueue(Base):
    __tablename__ = "approval_queue"
    id, change_type (db_schema/business_knowledge), title, diff_data (JSON),
    status (PENDING/APPROVED/REJECTED), requested_by, reviewed_by
```

### `ResolvedTerminology` (`resolved_terminology.py`)
```python
class ResolvedTerminology(Base):
    __tablename__ = "resolved_terminology"
    id, term (unique), resolved_value
```

---

## 17. Frontend Architecture

### `CopilotPage.tsx` (774 lines)

Main React page using:
- **Zustand stores**: `useChatStore`, `useThemeStore`, `useAuthStore`
- **Streaming**: `useStreamingQuery` hook with real-time callbacks
- **ECharts**: `echarts-for-react` for chart rendering

**Key components:**
- **Chat area**: scrollable message list with markdown + chart rendering
- **ChatInput**: text box with send/stop buttons
- **Agent sidebar**: per-run node-level details (tables, SQL, row count, intent, insights)
- **Transparency panel**: real-time pipeline steps as they complete
- **DisambiguationModal**: pops up when `DISAMBIGUATION_NEEDED:` detected
- **WelcomeScreen**: datasource-specific suggested queries
- **Timer**: elapsed loading time with 7-step progress bar
- **Chat modes**: Chat / SQL / Dashboard (tab navigation)

**Suggestions generator:**
```typescript
function generateSuggestedQueries(datasourceId: string) {
    if (datasourceId === "limese") {
        // Beauty e-commerce suggestions
    } else if (datasourceId === "default") {
        // SQLite demo suggestions
    } else {
        // Generic suggestions
    }
}
```

### `store/chat.ts` (246 lines)

Zustand store with **localStorage persistence** (`analytics-copilot-chat-storage`):
```typescript
interface ChatSession {
    id: string;
    conversationId: string;
    messages: ChatMessage[];
    title: string;
    initiatedAt: number;
    updatedAt: number;
    pinned: boolean;
}

interface ChatMessage {
    id: string;
    role: "user" | "assistant";
    content: string;
    chart?: object;
    insights?: string[];
    key_metrics?: object;
    sql?: string;
    row_count?: number;
    viz_type?: string;
    columns?: string[];
    rows?: object[];
    follow_up_questions?: string[];
    error?: string;
    timestamp: number;
}
```

**Key methods:**
- `startNewSession()` → null activeSessionId
- `addUserMessage()` → auto-creates session with title from first 30 chars
- `addAssistantMessage()` → appends with all structured data
- `deleteMessage()` → removes user+assistant pair (2 messages)
- `purgeExpiredSessions()` → removes sessions older than 10 min
- `togglePin()` → pinned sort first

### `api/client.ts` (111 lines)

Axios configuration:
```typescript
const api = axios.create({
    baseURL: '/api/v1',
    timeout: 90000,  // 90 seconds
});

// Request interceptor: attaches Bearer token
// Response interceptor: auto-refresh on 401, retry once
```

**API functions:**
```typescript
sendQuery(question, datasourceId, conversationId)    // POST /copilot/query
uploadFile(file)                                      // POST /copilot/upload (multipart)
getHistory(conversationId)                            // GET /copilot/history/{id}
getSchema(datasourceId)                               // GET /copilot/schema/{id}
getDatasources()                                      // GET /copilot/datasources
```

### Components (`components/Chat/`)

| File | Purpose |
|------|---------|
| `ChatInput.tsx` | Input box with send/stop buttons, Enter to send |
| `ChatMessage.tsx` | Renders markdown text, ECharts chart, insight bullets, follow-up chips, SQL code block |
| `CommunicationAgent.tsx` | Real-time thinking indicator with step-by-step progress |
| `CopilotWidget.tsx` | Embeddable widget version for external use |

---

## 18. End-to-End Request Flow

```
User: "Show me revenue by platform for 2025"
       │
       ▼
1. POST /api/v1/copilot/query
   ├─ Security keyword check → pass
   ├─ LLM cache check → miss (first time)
   │
   ▼
2. run_analytics_agent()
   ├─ Load MinIO history → none
   ├─ Compact history → empty
   ├─ Qdrant semantic cache → miss
   │
   ▼
3. check_qa_memory (Graph entry)
   ├─ QA memory → miss
   └─ Returns: {}
       │
       ▼
4. supervisor
   ├─ State: intent=empty, no schema, no SQL
   ├─ LLM decision: "understand_intent"
   │
   ▼
5. understand_intent
   ├─ pre_filter → "llm_classify" (not greeting)
   ├─ LLM (fast model) → type="chart_request", chart_type_hint="bar",
   │   time_range="2025", aggregation="sum",
   │   rephrased_question="Show total revenue by platform for the year 2025"
   └─ Returns: {intent: {...}}
       │
       ▼
6. route_tables (parallel)
   ├─ score_tables(question, user_history)
   ├─ combined_sales_final → score 25 (revenue + platform + sales)
   ├─ platform_sku_mapping → score 18 (platform)
   ├─ No ambiguity (clear intent)
   └─ Returns: {candidate_tables: [...], ambiguity_score: 0.0}
       │
       ▼
7. supervisor
   ├─ Has intent, no schema → "discover_schema"
       │
       ▼
8. discover_schema
   ├─ get_schema("limese") → cached ClickHouse schema
   ├─ _keyword_select_tables:
   │   combined_sales_final → revenue(+10) + platform keyword
   │   product_master → product? no exact match
   ├─ Returns: [combined_sales_final]
   ├─ Record table access in user history
   └─ Returns: {schema_context: {relevant_tables: [...], all_tables: [...]}}
       │
       ▼
9. supervisor
   ├─ Has schema, no SQL → "generate_sql"
       │
       ▼
10. generate_sql
    ├─ DynamicSchemaAgent scans question → no missing tables
    ├─ DB Intelligence: build_sql_context_prompt() → compact schema
    ├─ Business RAG: build_rag_prompt() → platform context
    ├─ Qdrant similar queries → 2 examples (filtered: revenue only)
    ├─ Date parser: 2025 → strict year filter warning
    ├─ Column selection: revenue only → DO NOT select units
    ├─ 14 ClickHouse rules injected
    ├─ LLM (smart model) generates:
    │   SELECT csf.sales_platform AS platform,
    │          SUM(csf.row_subtotal) AS revenue
    │   FROM combined_sales_final csf
    │   WHERE csf.date_created >= '2025-01-01'
    │     AND csf.date_created <= '2025-12-31'
    │     AND csf.final_status NOT IN ('cancelled',...,'Returned')
    │   GROUP BY csf.sales_platform
    │   ORDER BY revenue DESC
    │   LIMIT 50
    ├─ SQL safety (5 layers) → pass
    └─ Returns: {sql_query: "...", sql_explanation: "..."}
        │
        ▼
11. supervisor
    ├─ Has SQL, not validated → "review_sql"
        │
        ▼
12. review_sql (DBA)
    ├─ Fast regex: cancelled exclusion ✓, revenue column ✓, no lag() ✓
    ├─ LLM review: semantic check → pass
    └─ Returns: {sql_validated: True}
        │
        ▼
13. supervisor
    ├─ SQL validated, no results → "execute_sql"
        │
        ▼
14. execute_sql
    ├─ execute_query("limese", sql) → ClickHouse
    ├─ Returns 4 rows: [{platform: "Nykaa", revenue: 200000000}, ...]
    ├─ No errors → first attempt succeeded
    └─ Returns: {query_results: {columns: [...], rows: [...], row_count: 4}}
        │
        ▼
15. supervisor
    ├─ Has results, no insights → "analyze_insights"
    ├─ Has results, no viz → "generate_viz_config"
        │
        ├─► analyze_insights
        │   ├─ _compute_basic_stats() → client-side stats
        │   ├─ LLM generates insights + metrics + anomalies
        │   └─ Returns: {insights: ["Nykaa leads..."], key_metrics: {...}}
        │
        └─► generate_viz_config
            ├─ Classify: platform=str, revenue=num
            ├─ No pivot needed (2 columns)
            ├─ Build bar chart ECharts config
            └─ Returns: {viz_config: {type: "bar", xAxis: {...}, series: [...]}, viz_type: "bar"}
                │
                ▼
16. supervisor
    ├─ Has insights, not validated → "review_insights"
        │
        ▼
17. review_insights (Critic)
    ├─ Cross-reference insights with raw 4 rows
    ├─ Check: Nykaa is actually highest? ✓
    ├─ Check: numbers match? ✓
    └─ Returns: {insights_validated: True}
        │
        ▼
18. supervisor
    ├─ All complete → "compose_response"
        │
        ▼
19. compose_response
    ├─ LLM generates executive summary + follow-ups
    ├─ Stores in Qdrant (vector memory)
    ├─ Stores in QA memory
    ├─ Stores in MinIO
    └─ Returns: final_response dict
        │
        ▼
20. POST /api/v1/copilot/query response
    ├─ text: "In 2025, Nykaa generated the highest revenue..."
    ├─ chart: ECharts bar config
    ├─ insights: ["Nykaa leads with ₹200 Cr...", ...]
    ├─ key_metrics: {total_revenue: "₹563.95 Cr"}
    ├─ follow_up_questions: ["Show monthly revenue trend...", ...]
    └─ sql: "SELECT ..."
```

---

## 19. Security Architecture

### Three-Layer Read-Only Enforcement

```
Layer 1: SQL Generation (sql_gen.py)
├─ 5 validation layers:
│   1. Must start with SELECT
│   2. 20+ forbidden patterns (DROP, DELETE, INSERT, ALTER, etc.)
│   3. No multi-statement (semicolons + keywords)
│   4. No comment injection
│   5. No dangerous functions (system, exec, eval)

Layer 2: Connector Execution (connector.py)
├─ Re-checks all SQL before execution
├─ PermissionError on violation (logged)

Layer 3: Database Server Permissions
├─ ClickHouse user is read-only from the DB side
└─ SQLite runs with restricted permissions
```

### Input Security (copilot.py)

- Query endpoint checks input question for dangerous keywords before agent invocation
- Returns user-friendly error message instead of technical details

### API Key Validation (llm.py)

```python
def _is_valid_key(key):
    # Rejects: None, empty, wildcards, placeholder text, "your_" prefixes
```

### Authentication (JWT)

- Optional JWT auth via `python-jose`
- Bearer token attached by frontend Axios interceptor
- Auto-refresh on 401, retry original request once
- Role-based features: SQL visibility for business_analyst only

---

## 20. Caching Strategy

### Three Cache Layers

| Layer | Technology | TTL | Purpose |
|-------|-----------|-----|---------|
| **Qdrant Vector** | Qdrant + FastEmbed | Permanent | Semantic similarity cache (same meaning, different wording) |
| **LLM Canary** | Memory + Redis | 15 minutes | Exact question dedup within short window |
| **QA Memory** | Memory + Disk | Permanent | Identical question match |

### Cache Invalidation Rules

- **Qdrant**: Validates year/month matches between current and cached question; rejects if one asks for trend and other doesn't
- **LLM Canary**: Rejects results with 0 rows (forces fresh agent run); LRU eviction at 200 entries
- **QA Memory**: Only used for exact match; cleared on schema changes

### Schema Cache

| Cache | Location | TTL | Persistence |
|-------|----------|-----|------------|
| Connector Schema | `schema_cache.json` | 1 hour | Memory + Disk |
| DB Intelligence | `db_intelligence.json` | 24 hours | Memory + Disk |

### Cache Storage Hierarchy

```
Qdrant ──→ Semantic vector embeddings (BAAI/bge-base-en-v1.5)
Redis ───→ LLM Canary cache (short TTL, fast key-value)
Memory ──→ In-process LRU (200 entries)
Disk ────→ Schema cache, DB intelligence, Business RAG
```
