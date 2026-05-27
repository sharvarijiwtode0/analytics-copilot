"""
Data Visualization Copilot — FastAPI Backend
Entry point: uvicorn backend.main:app --reload --port 8001
# Force reload trigger: Dynamic LLM timeouts, regex JSON parsing, and refined gauge heuristics v2
"""
from __future__ import annotations

import structlog
import uvicorn
from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from backend.config import settings
from backend.database import init_db
from backend.routers import copilot, dashboards, canary_compat, streaming, auth, knowledge, admin, analytics, reports
from backend.data.connector import register_datasource

log = structlog.get_logger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Data Visualization Copilot",
        description="AI-powered analytics: ask questions in plain English, get interactive charts",
        version="1.0.0",
        docs_url="/docs",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router, prefix="/api/v1", tags=["Authentication"])
    app.include_router(admin.router, prefix="/api/v1", tags=["Admin"])
    app.include_router(analytics.router, prefix="/api/v1", tags=["Analytics"])
    app.include_router(knowledge.router, prefix="/api/v1", tags=["Knowledge Management"])
    app.include_router(copilot.router, prefix="/api/v1/copilot", tags=["AI Copilot"])
    app.include_router(streaming.router, prefix="/api/v1/copilot", tags=["AI Copilot - Streaming"])
    app.include_router(dashboards.router, prefix="/api/v1/dashboards", tags=["Dashboards"])
    app.include_router(reports.router, prefix="/api/v1", tags=["Reports"])
    app.include_router(canary_compat.router, prefix="/api/v1", tags=["Canary Compatible"])

    # DB Intelligence API endpoints

    @app.get("/api/v1/db/context", tags=["DB Intelligence"])
    async def get_context_summary() -> dict:
        """Get the current DB intelligence context (what the LLM knows about the database)."""
        from backend.services.db_intelligence import get_db_context
        ctx = get_db_context()
        # Return summary (not full context — could be large)
        summary = {
            "database": ctx.get("database"),
            "scanned_at": ctx.get("scanned_at"),
            "scan_duration_seconds": ctx.get("scan_duration_seconds"),
            "tables": {
                t: {
                    "row_count": data.get("row_count", 0),
                    "columns": len(data.get("columns", [])),
                    "business_facts": data.get("business_facts"),
                }
                for t, data in ctx.get("tables", {}).items()
            },
            "global_notes_count": len(ctx.get("global_notes", [])),
        }
        return summary

    @app.post("/api/v1/db/context/refresh", tags=["DB Intelligence"])
    async def refresh_context(background_tasks: BackgroundTasks) -> dict:
        """Trigger a fresh database scan. Runs in background — returns immediately."""
        from backend.services.db_intelligence import get_db_context

        def _refresh():
            get_db_context(force_refresh=True)

        background_tasks.add_task(_refresh)
        return {"status": "refresh_started", "message": "Scanning database in background. Check /api/v1/db/context in ~60 seconds."}

    @app.post("/api/v1/db/scan", tags=["DB Intelligence"])
    async def scan_database(
        database_url: str,
        output_format: str = "json",  # json, markdown, or both
        background_tasks: BackgroundTasks = None
    ) -> dict:
        """
        Scan any database and generate comprehensive documentation.

        This endpoint creates a README-style documentation that helps understand:
        - All tables and their structures
        - Column types, constraints, and sample values
        - Foreign key relationships
        - Join patterns between tables

        STORAGE: Documentation is saved to ./data/db_scans/{scan_id}/
        Contains: documentation.json, README.md, llm_context.txt

        Example:
            POST /api/v1/db/scan?database_url=sqlite:///./dvc.db&output_format=both
        """
        from backend.services.universal_db_scanner import scan_datasource, save_scan_to_storage
        import uuid

        # Generate unique scan ID
        scan_id = str(uuid.uuid4())[:8]

        if background_tasks and output_format != "markdown":
            # Run in background for large databases
            def _scan():
                try:
                    doc = scan_datasource(database_url, None)
                    save_scan_to_storage(doc, scan_id)
                    log.info("db_scan.complete", scan_id=scan_id)
                except Exception as e:
                    log.error("db_scan.failed", scan_id=scan_id, error=str(e))

            background_tasks.add_task(_scan)
            return {
                "status": "scan_started",
                "scan_id": scan_id,
                "message": f"Database scan running in background. Results will be saved to ./data/db_scans/{scan_id}/"
            }
        else:
            # Run synchronously for small databases or markdown-only requests
            try:
                from backend.services.universal_db_scanner import UniversalDatabaseScanner

                doc = scan_datasource(database_url, None)
                storage_info = save_scan_to_storage(doc, scan_id)

                result = {
                    "status": "complete",
                    "scan_id": scan_id,
                    "database": doc.database_name,
                    "database_type": doc.database_type,
                    "tables_count": len(doc.tables),
                    "relationships_count": len(doc.relationships),
                    "storage_path": storage_info["storage_path"],
                }

                if output_format in ("markdown", "both"):
                    scanner = UniversalDatabaseScanner(database_url)
                    result["markdown"] = scanner.generate_markdown_readme(doc)
                    result["llm_context"] = scanner.generate_llm_context(doc)

                return result
            except Exception as e:
                return {
                    "status": "error",
                    "error": str(e),
                    "message": f"Failed to scan database. Check the database URL and credentials."
                }

    @app.get("/api/v1/db/scan/{scan_id}", tags=["DB Intelligence"])
    async def get_scan_results(scan_id: str) -> dict:
        """Retrieve results of a previous database scan."""
        from pathlib import Path
        from backend.services.universal_db_scanner import DB_SCANS_PATH

        scan_dir = DB_SCANS_PATH / scan_id
        json_path = scan_dir / "documentation.json"
        md_path = scan_dir / "README.md"

        if not json_path.exists():
            return {"error": "Scan not found", "scan_id": scan_id}

        import json
        with open(json_path) as f:
            doc = json.load(f)

        result = {
            "scan_id": scan_id,
            "database": doc.get("database_name"),
            "scanned_at": doc.get("scanned_at"),
            "storage_path": str(scan_dir),
            "tables": {k: {"row_count": v.get("row_count"), "columns": len(v.get("columns", []))}
                      for k, v in doc.get("tables", {}).items()},
            "relationships": doc.get("relationships", []),
        }

        if md_path.exists():
            result["markdown_path"] = str(md_path)

        return result

    # Detect built frontend
    from fastapi.responses import RedirectResponse, HTMLResponse

    FRONTEND_DIRS = ["backend/static", "frontend/dist", "static"]
    FRONTEND_DIR = next(
        (d for d in FRONTEND_DIRS if os.path.isdir(d) and os.path.exists(f"{d}/index.html")),
        None
    )

    # Mount frontend LAST so FastAPI's own routes take priority
    # StaticFiles with html=True serves index.html for unknown paths (SPA routing)
    if FRONTEND_DIR:
        log.info("frontend.served", dir=FRONTEND_DIR)

    @app.on_event("startup")
    async def startup() -> None:
        await init_db()

        # Seed default admin user if users table is empty
        await _seed_admin_user()

        # Demo SQLite datasource
        register_datasource("default", "sqlite", {"path": "./demo.db"})
        await _seed_demo_data()

        # Limese ClickHouse — all real sales, inventory, product data
        register_datasource("limese", "clickhouse", {
            "host": "118.95.209.221",
            "port": 8123,
            "username": "limese_interns",
            "password": "ItsInterns!23",
            "database": "limese",
        })
        log.info("dvc.started", port=8001, datasources=["default (demo)", "limese (clickhouse)"])

        # Start DB Intelligence Layer — scans Limese ClickHouse on startup,
        # caches context to disk, refreshes every 6 hours automatically.
        from backend.services.db_intelligence import start_background_refresh
        start_background_refresh()

        # Import QueryLog model to ensure it's included in database metadata
        from backend.models.query_log import QueryLog

    @app.get("/health", tags=["System"])
    async def health() -> dict:
        return {"status": "healthy", "app": settings.app_name, "version": "1.0.0"}

    # Mount frontend AFTER all API routes so FastAPI routes take priority.
    if FRONTEND_DIR:
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

        # SPA fallback: any 404 that is NOT an API path → serve index.html
        from starlette.exceptions import HTTPException as StarletteHTTPException

        @app.exception_handler(StarletteHTTPException)
        async def spa_fallback(request, exc: StarletteHTTPException) -> HTMLResponse:
            path = request.url.path
            # Return normal error for API routes
            if path.startswith(("/api/", "/docs", "/openapi", "/health")):
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            # SPA routes → always serve index.html
            if exc.status_code == 404:
                with open(f"{FRONTEND_DIR}/index.html") as f:
                    return HTMLResponse(f.read())
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    return app


async def _seed_admin_user() -> None:
    """Create a default admin user if no users exist in the database."""
    from backend.database import AsyncSessionLocal as async_session_factory
    from backend.models.user import User
    from sqlalchemy import select, func

    # Default credentials — change via Admin Panel after first login
    DEFAULT_ADMIN_EMAIL = "admin@demo.com"
    DEFAULT_ADMIN_PASSWORD = "admin123"
    DEFAULT_ADMIN_NAME = "Admin"

    try:
        async with async_session_factory() as session:
            result = await session.execute(select(func.count()).select_from(User))
            count = result.scalar()
            if count == 0:
                admin = User(
                    email=DEFAULT_ADMIN_EMAIL,
                    name=DEFAULT_ADMIN_NAME,
                    hashed_password=User.hash_password(DEFAULT_ADMIN_PASSWORD),
                    role="admin",
                    is_active=True,
                )
                session.add(admin)
                await session.commit()
                log.info(
                    "admin_user.seeded",
                    email=DEFAULT_ADMIN_EMAIL,
                    note="Change password after first login via Admin Panel",
                )
            else:
                log.info("admin_user.exists", count=count)
    except Exception as e:
        log.error("admin_user.seed_failed", error=str(e))


async def _seed_demo_data() -> None:
    """Create a demo SQLite database with realistic sample data."""
    import sqlite3, random
    from datetime import datetime, timedelta

    conn = sqlite3.connect("./demo.db")
    c = conn.cursor()

    # Sales table
    c.execute("""CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY, date TEXT, region TEXT, product TEXT,
        category TEXT, revenue REAL, units INTEGER, cost REAL
    )""")

    # Users table
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY, name TEXT, email TEXT, signup_date TEXT,
        plan TEXT, country TEXT, monthly_spend REAL, is_active INTEGER
    )""")

    # Tickets table
    c.execute("""CREATE TABLE IF NOT EXISTS support_tickets (
        id INTEGER PRIMARY KEY, created_at TEXT, status TEXT, priority TEXT,
        category TEXT, resolution_time_hours REAL, agent TEXT, rating INTEGER
    )""")

    # Only seed if empty
    if c.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 0:
        regions = ["North India", "South India", "West India", "East India", "International"]
        products = ["Analytics Pro", "DataSync", "ReportBuilder", "AIInsights", "Dashboard Plus"]
        categories = ["SaaS", "Enterprise", "Startup", "SMB"]

        base_date = datetime(2025, 1, 1)
        for i in range(2000):
            d = base_date + timedelta(days=random.randint(0, 500))
            c.execute("INSERT INTO sales VALUES (?,?,?,?,?,?,?,?)", (
                i+1, d.strftime("%Y-%m-%d"),
                random.choice(regions),
                random.choice(products),
                random.choice(categories),
                round(random.uniform(5000, 150000), 2),
                random.randint(1, 50),
                round(random.uniform(1000, 50000), 2),
            ))

        plans = ["Free", "Starter", "Professional", "Enterprise"]
        countries = ["India", "USA", "UK", "Singapore", "UAE", "Australia"]
        for i in range(500):
            d = base_date + timedelta(days=random.randint(0, 500))
            c.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,?)", (
                i+1, f"User {i+1}", f"user{i+1}@example.com",
                d.strftime("%Y-%m-%d"),
                random.choice(plans),
                random.choice(countries),
                round(random.uniform(0, 50000), 2),
                random.choice([0, 1, 1, 1]),
            ))

        priorities = ["Low", "Medium", "High", "Critical"]
        statuses = ["Open", "In Progress", "Resolved", "Closed"]
        ticket_cats = ["Billing", "Technical", "Account", "Feature Request", "Bug"]
        agents = ["Alex", "Morgan", "Rahul", "Priya", "Sam"]
        for i in range(1000):
            d = base_date + timedelta(days=random.randint(0, 500))
            c.execute("INSERT INTO support_tickets VALUES (?,?,?,?,?,?,?,?)", (
                i+1, d.strftime("%Y-%m-%dT%H:%M:%S"),
                random.choice(statuses),
                random.choice(priorities),
                random.choice(ticket_cats),
                round(random.uniform(0.5, 72), 1),
                random.choice(agents),
                random.randint(1, 5),
            ))

        conn.commit()
        log.info("demo_data.seeded", sales=2000, users=500, tickets=1000)

    conn.close()


app = create_app()

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8001, reload=True)
