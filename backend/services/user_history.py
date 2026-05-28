"""
User History Service.
Tracks user domain query histories to prioritize table routing.
Saves to a local JSON cache to ensure 0ms latency during runtime.
"""
from __future__ import annotations
import json
from pathlib import Path
import structlog

log = structlog.get_logger(__name__)

HISTORY_FILE = Path(__file__).parent.parent / "data" / "user_history.json"
HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

# In-memory history cache: {user_id_or_session_id: {domain: count}}
_history_cache: dict[str, dict[str, int]] = {}


def _load_history() -> None:
    global _history_cache
    if not _history_cache and HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r") as f:
                _history_cache = json.load(f)
            log.info("user_history.loaded", path=str(HISTORY_FILE), users=len(_history_cache))
        except Exception as exc:
            log.warning("user_history.load_failed", error=str(exc))


def _save_history() -> None:
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(_history_cache, f, indent=2)
    except Exception as exc:
        log.warning("user_history.save_failed", error=str(exc))


def get_user_profile(user_id: str, session_id: str | None = None) -> dict[str, float]:
    """
    Returns normalized frequency weights per domain for the user: {domain: boost_weight}.
    Weights are float values from 0.0 to 1.0.
    """
    _load_history()
    
    # Identify user key (prefer persistent user_id, fallback to ephemeral session_id)
    key = user_id if user_id and user_id != "anonymous" else session_id
    if not key or key not in _history_cache:
        return {}

    user_counts = _history_cache[key]
    total = sum(user_counts.values())
    if total == 0:
        return {}

    # Normalize: more frequent domains get higher weight (cap at 1.0)
    return {domain: round(count / total, 2) for domain, count in user_counts.items()}


def record_table_access(user_id: str, table_names: list[str], session_id: str | None = None) -> None:
    """Increment domain query frequencies for the user based on selected tables."""
    from backend.services.table_catalog import get_table_blueprint

    if not table_names:
        return

    _load_history()

    key = user_id if user_id and user_id != "anonymous" else session_id
    if not key:
        return

    if key not in _history_cache:
        _history_cache[key] = {}

    user_counts = _history_cache[key]

    for table in table_names:
        bp = get_table_blueprint(table)
        domain = bp.get("domain", "staging")
        # Ignore staging tables in user profiles
        if domain != "staging":
            user_counts[domain] = user_counts.get(domain, 0) + 1

    # Keep user history focused: only preserve top 5 domains
    if len(user_counts) > 10:
        sorted_domains = sorted(user_counts.items(), key=lambda x: -x[1])
        _history_cache[key] = dict(sorted_domains[:5])

    _save_history()
    log.info("user_history.updated", key=key, domains=list(user_counts.keys()))
