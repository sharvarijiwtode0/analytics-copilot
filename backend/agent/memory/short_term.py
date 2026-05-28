# Short‑term memory for the Analytics Copilot

"""A lightweight in‑memory store that keeps the most recent interactions for a
session. This complements the persistent Qdrant vector memory and enables fast
access to the last few queries without embedding overhead.

The store is deliberately simple – a dictionary mapping `session_id` to a list
of entries. Each entry is a dict containing the user question, generated SQL,
and a timestamp. The size is capped at `MAX_ENTRIES` (default 10) per session.
"""

import time
from collections import defaultdict
from typing import List, Dict

MAX_ENTRIES = 10


class ShortTermMemory:
    def __init__(self) -> None:
        # Mapping: session_id -> list of recent entries (newest last)
        self._store: Dict[str, List[dict]] = defaultdict(list)

    def add_entry(self, session_id: str, question: str, sql: str, payload: dict) -> None:
        """Add a new record for the given session.

        The payload is copied (shallow) and enriched with `question` and `sql`
        for convenience. Old entries beyond `MAX_ENTRIES` are dropped.
        """
        entry = {
            "question": question,
            "sql": sql,
            "payload": dict(payload),
            "timestamp": time.time(),
        }
        self._store[session_id].append(entry)
        # Trim excess entries
        if len(self._store[session_id]) > MAX_ENTRIES:
            self._store[session_id] = self._store[session_id][-MAX_ENTRIES:]

    def get_recent(self, session_id: str, limit: int = 5) -> List[dict]:
        """Return the most recent `limit` entries for the session.

        If the session does not exist an empty list is returned.
        """
        return list(reversed(self._store.get(session_id, [])[-limit:]))

    def clear_session(self, session_id: str) -> None:
        """Remove all stored entries for a session (e.g., when a conversation ends)."""
        self._store.pop(session_id, None)
