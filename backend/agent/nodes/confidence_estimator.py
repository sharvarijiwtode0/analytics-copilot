# Confidence Estimator Node
"""Estimates a confidence score for the current analytics pipeline run.

The score is used by the recursive supervisor to decide whether to
re‑run earlier nodes (intent, SQL generation, etc.).

For now the implementation is a simple heuristic:
- If the intent node supplied a `confidence` field, use it.
- Otherwise, derive a baseline from the presence of insights and the
  semantic‑cache hit score (if any).
- The resulting float is stored in `state["confidence"]`.

Future versions can replace this with a lightweight LLM call or a more
advanced statistical model.
"""

from __future__ import annotations

from typing import TypedDict

from backend.agent.state import AnalyticsState
from backend.config import settings


async def confidence_estimator(state: AnalyticsState) -> AnalyticsState:
    """Compute and attach a confidence score to the pipeline state.

    Args:
        state: The shared `AnalyticsState` dictionary.

    Returns:
        The mutated `state` with a new `confidence` key (float) and optional
        retry bookkeeping fields.
    """
    # 1️⃣ Prefer explicit confidence from the intent node if present
    intent_conf = state.get("intent", {}).get("confidence")
    if isinstance(intent_conf, (float, int)):
        confidence = float(intent_conf)
    else:
        # 2️⃣ Fallback heuristic based on available insights and cache hit
        insights_present = bool(state.get("insights"))
        cache_hit_score = 0.0
        # If a semantic cache hit was recorded, it may have a score attribute
        # stored in the state under a custom key by the cache lookup node.
        cache_info = state.get("semantic_cache_hit_score")
        if isinstance(cache_info, (float, int)):
            cache_hit_score = float(cache_info)
        # Simple weighted sum (tunable later)
        confidence = 0.6 + (0.2 if insights_present else 0.0) + (0.2 * cache_hit_score)
        confidence = min(max(confidence, 0.0), 1.0)

    # Store confidence and ensure retry counters exist
    state["confidence"] = confidence
    state.setdefault("retry_count", 0)
    state.setdefault("max_retries", getattr(settings, "RECURSIVE_MAX_RETRIES", 3))
    return state
