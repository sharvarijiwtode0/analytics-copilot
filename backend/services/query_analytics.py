"""Query Analytics Service - Log queries and provide analytics aggregations."""
import structlog
from datetime import datetime, timedelta
from sqlalchemy import select, func, and_, Integer, desc
from sqlalchemy.ext.asyncio import AsyncSession
from backend.models.query_log import QueryLog

log = structlog.get_logger(__name__)


async def log_query(
    db: AsyncSession,
    user_id: str,
    conversation_id: str,
    datasource_id: str,
    question: str,
    intent_type: str,
    sql_query: str | None,
    row_count: int,
    viz_type: str | None,
    latency_ms: int,
    cache_hit: bool,
    model_used: str,
    error: str | None = None
) -> QueryLog:
    """Create a query log entry."""
    query_log = QueryLog(
        user_id=user_id,
        conversation_id=conversation_id,
        datasource_id=datasource_id,
        question=question,
        intent_type=intent_type,
        sql_query=sql_query,
        row_count=row_count,
        viz_type=viz_type,
        latency_ms=latency_ms,
        cache_hit=cache_hit,
        model_used=model_used,
        error=error,
        success=error is None
    )
    db.add(query_log)
    await db.commit()
    await db.refresh(query_log)
    log.info("query_analytics.logged", user_id=user_id, intent_type=intent_type, cache_hit=cache_hit)
    return query_log


async def get_user_summary(
    db: AsyncSession,
    user_id: str,
    days: int = 30
) -> dict:
    """Get query summary for a specific user."""
    since = datetime.utcnow() - timedelta(days=days)

    result = await db.execute(
        select(
            func.count(QueryLog.id).label('total_queries'),
            func.sum(func.cast(QueryLog.cache_hit, type_=Integer)).label('cache_hits'),
            func.avg(QueryLog.latency_ms).label('avg_latency'),
            func.sum(func.cast(QueryLog.success, type_=Integer)).label('successful')
        ).where(
            and_(
                QueryLog.user_id == user_id,
                QueryLog.created_at >= since
            )
        )
    )
    row = result.one()

    total = row.total_queries or 0
    cache_hits = row.cache_hits or 0
    avg_latency = row.avg_latency or 0

    return {
        "total_queries": total,
        "cache_hit_rate": round(cache_hits / total * 100, 1) if total > 0 else 0,
        "avg_latency_ms": round(avg_latency, 1),
        "success_rate": round((row.successful or 0) / total * 100, 1) if total > 0 else 0,
        "days": days
    }


async def get_daily_stats(
    db: AsyncSession,
    user_id: str | None = None,
    days: int = 30
) -> list[dict]:
    """Get daily query counts for the last N days."""
    since = datetime.utcnow() - timedelta(days=days)

    query = select(
        func.date(QueryLog.created_at).label('date'),
        func.count(QueryLog.id).label('count'),
        func.sum(func.cast(QueryLog.cache_hit, type_=Integer)).label('cache_hits')
    ).where(QueryLog.created_at >= since)

    if user_id:
        query = query.where(QueryLog.user_id == user_id)

    query = query.group_by(func.date(QueryLog.created_at)).order_by(func.date(QueryLog.created_at))

    result = await db.execute(query)
    return [
        {
            "date": str(row.date),
            "count": row.count,
            "cache_hits": row.cache_hits or 0
        }
        for row in result
    ]


async def get_popular_queries(
    db: AsyncSession,
    user_id: str | None = None,
    limit: int = 10
) -> list[dict]:
    """Get most frequently asked questions."""

    query = select(
        QueryLog.question,
        func.count(QueryLog.id).label('count')
    ).group_by(QueryLog.question)

    if user_id:
        query = query.where(QueryLog.user_id == user_id)

    query = query.order_by(desc(func.count(QueryLog.id))).limit(limit)

    result = await db.execute(query)
    return [
        {"question": row.question, "count": row.count}
        for row in result
    ]


async def get_intent_distribution(
    db: AsyncSession,
    user_id: str | None = None
) -> list[dict]:
    """Get distribution of query intent types."""
    query = select(
        QueryLog.intent_type,
        func.count(QueryLog.id).label('count')
    ).group_by(QueryLog.intent_type)

    if user_id:
        query = query.where(QueryLog.user_id == user_id)

    query = query.order_by(desc(func.count(QueryLog.id)))

    result = await db.execute(query)
    return [
        {"intent_type": row.intent_type, "count": row.count}
        for row in result
    ]


async def get_admin_summary(
    db: AsyncSession,
    days: int = 30
) -> dict:
    """Get system-wide query summary (admin only)."""
    since = datetime.utcnow() - timedelta(days=days)

    result = await db.execute(
        select(
            func.count(QueryLog.id).label('total_queries'),
            func.count(func.distinct(QueryLog.user_id)).label('unique_users'),
            func.count(func.distinct(QueryLog.datasource_id)).label('datasources'),
            func.sum(func.cast(QueryLog.cache_hit, type_=Integer)).label('cache_hits'),
            func.avg(QueryLog.latency_ms).label('avg_latency'),
            func.sum(func.cast(QueryLog.success, type_=Integer)).label('successful')
        ).where(QueryLog.created_at >= since)
    )
    row = result.one()

    total = row.total_queries or 0
    cache_hits = row.cache_hits or 0
    avg_latency = row.avg_latency or 0

    return {
        "total_queries": total,
        "unique_users": row.unique_users or 0,
        "datasources": row.datasources or 0,
        "cache_hit_rate": round(cache_hits / total * 100, 1) if total > 0 else 0,
        "avg_latency_ms": round(avg_latency, 1),
        "success_rate": round((row.successful or 0) / total * 100, 1) if total > 0 else 0,
        "days": days
    }


async def get_user_activity(
    db: AsyncSession,
    days: int = 30
) -> list[dict]:
    """Get query count by user (admin only)."""
    since = datetime.utcnow() - timedelta(days=days)

    result = await db.execute(
        select(
            QueryLog.user_id,
            func.count(QueryLog.id).label('query_count'),
            func.avg(QueryLog.latency_ms).label('avg_latency')
        ).where(QueryLog.created_at >= since)
        .group_by(QueryLog.user_id)
        .order_by(func.count(QueryLog.id).desc())
    )
    return [
        {
            "user_id": row.user_id,
            "query_count": row.query_count,
            "avg_latency_ms": round(row.avg_latency, 1) if row.avg_latency else 0
        }
        for row in result
    ]


async def get_datasource_performance(
    db: AsyncSession,
    days: int = 30
) -> list[dict]:
    """Get query performance by datasource (admin only)."""
    since = datetime.utcnow() - timedelta(days=days)

    result = await db.execute(
        select(
            QueryLog.datasource_id,
            func.count(QueryLog.id).label('query_count'),
            func.avg(QueryLog.latency_ms).label('avg_latency'),
            func.sum(func.cast(QueryLog.success, type_=Integer)).label('successful')
        ).where(QueryLog.created_at >= since)
        .group_by(QueryLog.datasource_id)
        .order_by(func.count(QueryLog.id).desc())
    )
    return [
        {
            "datasource_id": row.datasource_id,
            "query_count": row.query_count,
            "avg_latency_ms": round(row.avg_latency, 1) if row.avg_latency else 0,
            "success_rate": round(row.successful / row.query_count * 100, 1) if row.query_count > 0 else 0
        }
        for row in result
    ]
