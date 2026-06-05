"""
Universal data connector.
Supports: PostgreSQL, SQLite, CSV/Excel, ClickHouse.
Schema is cached per datasource to avoid repeated introspection.
"""
from __future__ import annotations

import asyncio
import io
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from backend.config import settings

log = structlog.get_logger(__name__)

# In-memory schema cache: {datasource_id: {schema, refreshed_at}}
_schema_cache: dict[str, dict] = {}

# In-memory datasource registry (production: from DB)
# Format: {id: {type, connection_config}}
_datasources: dict[str, dict] = {}

SCHEMA_CACHE_FILE = Path(__file__).parent.parent / "data" / "schema_cache.json"


def _load_schema_cache_from_disk() -> None:
    global _schema_cache
    if SCHEMA_CACHE_FILE.exists():
        try:
            with open(SCHEMA_CACHE_FILE, "r") as f:
                data = json.load(f)
            for ds_id, val in data.items():
                if "refreshed_at" in val:
                    val["refreshed_at"] = datetime.fromisoformat(val["refreshed_at"])
            _schema_cache = data
            log.info("schema_cache.loaded_from_disk", path=str(SCHEMA_CACHE_FILE), count=len(data))
        except Exception as exc:
            log.warning("schema_cache.disk_load_failed", error=str(exc))


def _save_schema_cache_to_disk() -> None:
    try:
        SCHEMA_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        serializable_cache = {}
        for ds_id, val in _schema_cache.items():
            refreshed_at_str = val["refreshed_at"].isoformat() if isinstance(val.get("refreshed_at"), datetime) else str(val.get("refreshed_at", ""))
            serializable_cache[ds_id] = {
                "schema": val["schema"],
                "refreshed_at": refreshed_at_str
            }
        with open(SCHEMA_CACHE_FILE, "w") as f:
            json.dump(serializable_cache, f, indent=2, default=str)
        log.info("schema_cache.saved_to_disk", path=str(SCHEMA_CACHE_FILE))
    except Exception as exc:
        log.warning("schema_cache.disk_save_failed", error=str(exc))


def register_datasource(ds_id: str, ds_type: str, config: dict) -> None:
    """Register a datasource for use by the agent."""
    _datasources[ds_id] = {"type": ds_type, "config": config}
    log.info("datasource.registered", id=ds_id, type=ds_type)


def _get_datasource(ds_id: str) -> dict:
    from backend.config import settings
    if ds_id == "limese" and "limese" not in _datasources:
        register_datasource("limese", "clickhouse", {
            "host": settings.clickhouse_host,
            "port": settings.clickhouse_port,
            "username": settings.clickhouse_user,
            "password": settings.clickhouse_password,
            "database": settings.clickhouse_database,
        })
    elif ds_id == "default" and "default" not in _datasources:
        register_datasource("default", "sqlite", {"path": "./demo.db"})

    if not ds_id or ds_id not in _datasources:
        # Default: use the app's own SQLite DB for demo
        return {"type": "sqlite", "config": {"path": "./dvc.db"}}
    return _datasources[ds_id]


async def get_schema(datasource_id: str) -> dict:
    """Get schema for a datasource, using cache if available."""
    global _schema_cache
    if not _schema_cache:
        _load_schema_cache_from_disk()

    cached = _schema_cache.get(datasource_id)
    if cached:
        refreshed_at = cached.get("refreshed_at")
        if isinstance(refreshed_at, str):
            try:
                refreshed_at = datetime.fromisoformat(refreshed_at)
                cached["refreshed_at"] = refreshed_at
            except Exception:
                refreshed_at = datetime.utcnow()

        age_seconds = (datetime.utcnow() - refreshed_at).seconds
        if age_seconds < 3600:  # 1-hour cache
            return cached["schema"]

    ds = _get_datasource(datasource_id)
    schema = await _introspect_schema(ds)
    _schema_cache[datasource_id] = {"schema": schema, "refreshed_at": datetime.utcnow()}
    _save_schema_cache_to_disk()
    return schema


async def execute_query(datasource_id: str, sql: str, timeout: int = 30) -> dict:
    """
    Execute SQL and return results as {columns: [...], rows: [...]}.

    SECURITY: Read-only enforcement at connector level (Layer 2 defense).
    Even if SQL passes initial validation, it's re-checked here before execution.
    """
    # ─── SECURITY LAYER 2: Read-only enforcement at execution ────────────────
    if not await _is_readonly_query(sql):
        log.error("query.blocked_modification_attempt", sql_preview=sql[:200], datasource=datasource_id)
        raise PermissionError(
            "READ-ONLY ACCESS: Data modification is not allowed. "
            "Only SELECT queries are permitted. This action has been logged."
        )

    ds = _get_datasource(datasource_id)
    ds_type = ds.get("type", "sqlite")

    try:
        result = await asyncio.wait_for(
            _execute_on_datasource(ds_type, ds["config"], sql, timeout),
            timeout=timeout,
        )
        return result
    except asyncio.TimeoutError:
        raise RuntimeError(f"Query timed out after {timeout} seconds")


async def _is_readonly_query(sql: str) -> bool:
    """
    Verify that a query is read-only (SELECT only).
    This is a second layer of defense - even if SQL validation is bypassed,
    the connector will refuse to execute modifying queries.
    """
    if not sql:
        return False

    sql_upper = sql.upper().strip()

    # Must start with SELECT (WITH allowed for CTEs that end in SELECT)
    if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
        log.error("security.readonly_violation_no_select", sql_preview=sql[:100])
        return False

    # Check for modifying keywords anywhere in the query
    modifying_keywords = [
        "INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER",
        "CREATE", "GRANT", "REVOKE", "COMMIT", "ROLLBACK",
        "INTO OUTFILE", "INTO DUMPFILE", "LOAD DATA",
    ]

    for keyword in modifying_keywords:
        # Use word boundary to avoid false positives
        pattern = rf"\b{keyword}\b"
        import re
        if re.search(pattern, sql_upper):
            log.error("security.readonly_violation_keyword",
                      keyword=keyword,
                      sql_preview=sql[:100],
                      severity="CRITICAL")
            return False

    # Check for multi-statement attempts (semicolons followed by dangerous ops)
    if ";" in sql:
        parts = sql.split(";")
        if len(parts) > 1:
            for part in parts[1:]:
                if any(kw in part.upper() for kw in ["SELECT", "INSERT", "UPDATE", "DELETE", "DROP", "CREATE"]):
                    log.error("security.readonly_violation_multistatement", sql_preview=sql[:100])
                    return False

    return True


async def _execute_on_datasource(ds_type: str, config: dict, sql: str, timeout: int = 30) -> dict:
    if ds_type == "sqlite":
        return await _execute_sqlite(config, sql)
    if ds_type in ("postgresql", "postgres"):
        return await _execute_postgresql(config, sql)
    if ds_type == "csv":
        return await _execute_csv(config, sql)
    if ds_type == "clickhouse":
        return await _execute_clickhouse(config, sql, timeout)
    raise ValueError(f"Unsupported datasource type: {ds_type}")


async def _execute_sqlite(config: dict, sql: str) -> dict:
    db_path = config.get("path", ":memory:")
    loop = asyncio.get_event_loop()

    def _run():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            cursor.execute(sql)
            rows_raw = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = [dict(zip(columns, row)) for row in rows_raw]
            return {"columns": columns, "rows": rows}
        finally:
            conn.close()

    return await loop.run_in_executor(None, _run)


async def _execute_postgresql(config: dict, sql: str) -> dict:
    try:
        import asyncpg
    except ImportError:
        raise RuntimeError("asyncpg not installed. Run: pip install asyncpg")

    conn = await asyncpg.connect(
        host=config.get("host", "localhost"),
        port=config.get("port", 5432),
        database=config.get("database"),
        user=config.get("user"),
        password=config.get("password"),
    )
    try:
        records = await conn.fetch(sql)
        if not records:
            return {"columns": [], "rows": []}
        columns = list(records[0].keys())
        rows = [dict(r) for r in records]
        return {"columns": columns, "rows": rows}
    finally:
        await conn.close()


async def _execute_csv(config: dict, sql: str) -> dict:
    """Execute SQL against a CSV file using DuckDB."""
    try:
        import duckdb
    except ImportError:
        raise RuntimeError("duckdb not installed. Run: pip install duckdb")

    file_path = config.get("file_path", "")
    table_name = config.get("table_name", "data")

    loop = asyncio.get_event_loop()

    def _run():
        conn = duckdb.connect(":memory:")
        conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM read_csv_auto('{file_path}')")
        result = conn.execute(sql).fetchdf()
        return {
            "columns": list(result.columns),
            "rows": result.to_dict(orient="records"),
        }

    return await loop.run_in_executor(None, _run)


async def _execute_clickhouse(config: dict, sql: str, timeout: int = 30) -> dict:
    from backend.data.clickhouse_connector import ClickHouseConnector
    conn = ClickHouseConnector(
        host=config.get("host", "localhost"),
        port=int(config.get("port", 8123)),
        username=config.get("username", config.get("user", "default")),
        password=config.get("password", ""),
        database=config.get("database", config.get("dbname", "default")),
    )
    return await conn.execute(sql, timeout)


async def _introspect_schema(ds: dict) -> dict:
    """Get list of tables and columns from a datasource."""
    ds_type = ds.get("type", "sqlite")
    config = ds.get("config", {})

    if ds_type == "sqlite":
        return await _sqlite_schema(config)
    if ds_type in ("postgresql", "postgres"):
        return await _postgresql_schema(config)
    if ds_type == "csv":
        return await _csv_schema(config)
    if ds_type == "clickhouse":
        return await _clickhouse_schema(config)
    return {"tables": []}


async def _sqlite_schema(config: dict) -> dict:
    db_path = config.get("path", ":memory:")
    loop = asyncio.get_event_loop()

    def _run():
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = []
            for (table_name,) in cursor.fetchall():
                cursor.execute(f"PRAGMA table_info(`{table_name}`)")
                columns_raw = cursor.fetchall()

                cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`")
                row_count = cursor.fetchone()[0]

                # Calculate null counts for all columns
                null_counts = {}
                if row_count > 0 and columns_raw:
                    select_parts = [f"SUM(CASE WHEN `{row[1]}` IS NULL THEN 1 ELSE 0 END)" for row in columns_raw]
                    nulls_sql = f"SELECT {', '.join(select_parts)} FROM `{table_name}`"
                    try:
                        cursor.execute(nulls_sql)
                        row_vals = cursor.fetchone()
                        if row_vals:
                            for col_meta, val in zip(columns_raw, row_vals):
                                null_counts[col_meta[1]] = int(val or 0)
                    except Exception as e:
                        log.warning("schema.sqlite_null_count_failed", table=table_name, error=str(e))

                columns = [
                    {
                        "name": row[1],
                        "type": row[2],
                        "nullable": not row[3],
                        "primary_key": bool(row[5]),
                        "null_count": null_counts.get(row[1], 0)
                    }
                    for row in columns_raw
                ]

                # Get sample data
                cursor.execute(f"SELECT * FROM `{table_name}` LIMIT 3")
                sample_cols = [desc[0] for desc in (cursor.description or [])]
                sample_rows = [dict(zip(sample_cols, r)) for r in cursor.fetchall()]

                tables.append({
                    "name": table_name,
                    "columns": columns,
                    "row_count": row_count,
                    "sample_data": sample_rows[:2],
                })
            return {"tables": tables}
        finally:
            conn.close()

    return await loop.run_in_executor(None, _run)


async def _postgresql_schema(config: dict) -> dict:
    try:
        import asyncpg
    except ImportError:
        return {"tables": [], "error": "asyncpg not installed"}

    try:
        conn = await asyncpg.connect(**config)
        tables = []
        rows = await conn.fetch("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        for row in rows:
            table_name = row["table_name"]
            cols = await conn.fetch("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = $1 AND table_schema = 'public'
                ORDER BY ordinal_position
            """, table_name)
            count = await conn.fetchval(f'SELECT COUNT(*) FROM "{table_name}"')

            # Calculate null counts for all columns
            null_counts = {}
            if count > 0 and cols:
                select_parts = [f'SUM(CASE WHEN "{c["column_name"]}" IS NULL THEN 1 ELSE 0 END)' for c in cols]
                nulls_sql = f'SELECT {", ".join(select_parts)} FROM "{table_name}"'
                try:
                    row_vals = await conn.fetchrow(nulls_sql)
                    if row_vals:
                        for c, val in zip(cols, row_vals):
                            null_counts[c["column_name"]] = int(val or 0)
                except Exception as e:
                    log.warning("schema.postgresql_null_count_failed", table=table_name, error=str(e))

            columns = [
                {
                    "name": c["column_name"],
                    "type": c["data_type"],
                    "nullable": c["is_nullable"] == "YES",
                    "null_count": null_counts.get(c["column_name"], 0)
                }
                for c in cols
            ]
            tables.append({"name": table_name, "columns": columns, "row_count": count})
        await conn.close()
        return {"tables": tables}
    except Exception as exc:
        return {"tables": [], "error": str(exc)}


async def _clickhouse_schema(config: dict) -> dict:
    from backend.data.clickhouse_connector import ClickHouseConnector
    conn = ClickHouseConnector(
        host=config.get("host", "localhost"),
        port=int(config.get("port", 8123)),
        username=config.get("username", config.get("user", "default")),
        password=config.get("password", ""),
        database=config.get("database", config.get("dbname", "default")),
    )
    return await conn.get_schema()


async def _csv_schema(config: dict) -> dict:
    try:
        import duckdb
        file_path = config.get("file_path", "")
        table_name = config.get("table_name", "data")
        conn = duckdb.connect(":memory:")
        conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM read_csv_auto('{file_path}')")
        result = conn.execute(f"DESCRIBE {table_name}").fetchall()
        columns_raw = [{"name": r[0], "type": r[1]} for r in result]
        count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]

        # Calculate null counts for all columns
        null_counts = {}
        if count > 0 and columns_raw:
            select_parts = [f'SUM(CASE WHEN "{col["name"]}" IS NULL THEN 1 ELSE 0 END)' for col in columns_raw]
            nulls_sql = f'SELECT {", ".join(select_parts)} FROM {table_name}'
            try:
                row_vals = conn.execute(nulls_sql).fetchone()
                if row_vals:
                    for col, val in zip(columns_raw, row_vals):
                        null_counts[col["name"]] = int(val or 0)
            except Exception as e:
                log.warning("schema.csv_null_count_failed", table=table_name, error=str(e))

        columns = [
            {
                "name": col["name"],
                "type": col["type"],
                "nullable": True,
                "null_count": null_counts.get(col["name"], 0)
            }
            for col in columns_raw
        ]

        return {"tables": [{"name": table_name, "columns": columns, "row_count": count}]}
    except Exception as exc:
        return {"tables": [], "error": str(exc)}


async def upload_csv_as_datasource(file_bytes: bytes, filename: str, ds_id: str) -> dict:
    """Save uploaded CSV and register as a datasource."""
    upload_dir = Path("uploads")
    upload_dir.mkdir(exist_ok=True)
    file_path = upload_dir / filename

    with open(file_path, "wb") as f:
        f.write(file_bytes)

    register_datasource(ds_id, "csv", {"file_path": str(file_path), "table_name": "data"})
    schema = await get_schema(ds_id)
    return {"datasource_id": ds_id, "file_path": str(file_path), "schema": schema}


def get_registered_datasources() -> list[dict]:
    """Get list of registered datasources with passwords/secrets masked."""
    results = []
    if not _datasources:
        _datasources["default"] = {"type": "sqlite", "config": {"path": "./demo.db"}}
    for ds_id, ds in _datasources.items():
        # mask credentials
        safe_config = {}
        if "config" in ds:
            for k, v in ds["config"].items():
                if k in ("password", "secret", "token", "password_hash"):
                    safe_config[k] = "******"
                else:
                    safe_config[k] = v
        results.append({
            "id": ds_id,
            "type": ds["type"],
            "config": safe_config
        })
    return results

