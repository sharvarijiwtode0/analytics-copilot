"""
Shared utilities for agent nodes.
"""
from __future__ import annotations
import json
import re
import structlog

log = structlog.get_logger(__name__)


def resilient_json_loads(text: str):
    """
    Highly resilient JSON extractor and parser.

    Handles LLM outputs that contain:
    - Markdown code blocks (```json ... ```)
    - Conversational text before/after the JSON
    - Trailing commas in objects/arrays
    - Single-line (// ...) and multi-line (/* ... */) JS-style comments
    """
    if not text:
        raise ValueError("Empty text input")

    cleaned = text.strip()

    # 1. Fast path — try direct parse first
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 2. Strip markdown code blocks
    codeblock_match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", cleaned, re.IGNORECASE)
    if codeblock_match:
        candidate = codeblock_match.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            cleaned = candidate  # continue cleaning this candidate

    # 3. Find outermost JSON structure { ... } or [ ... ]
    first_brace = cleaned.find("{")
    first_bracket = cleaned.find("[")

    start_pos = -1
    end_pos = -1

    if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
        start_pos = first_brace
        end_pos = cleaned.rfind("}")
    elif first_bracket != -1:
        start_pos = first_bracket
        end_pos = cleaned.rfind("]")

    if start_pos != -1 and end_pos != -1 and end_pos > start_pos:
        json_candidate = cleaned[start_pos : end_pos + 1]

        # 4. Remove single-line JS comments
        json_candidate = re.sub(r"//.*$", "", json_candidate, flags=re.MULTILINE)

        # 5. Remove multi-line JS comments
        json_candidate = re.sub(r"/\*[\s\S]*?\*/", "", json_candidate)

        # 6. Remove trailing commas before } or ]
        json_candidate = re.sub(r",\s*\}", "}", json_candidate)
        json_candidate = re.sub(r",\s*\]", "]", json_candidate)

        try:
            return json.loads(json_candidate.strip())
        except json.JSONDecodeError as exc:
            log.debug(
                "resilient_json.parse_failed",
                error=str(exc),
                sample=json_candidate[:200],
            )

    # Final attempt — let it raise naturally for the caller to catch
    return json.loads(cleaned)


def validate_cache_match(q1: str, q2: str) -> bool:
    """
    Validates if two questions are semantically equivalent beyond basic similarity.
    Ensures years, months, platforms, trends/charts, categories, and metrics match perfectly.
    """
    q1_lower, q2_lower = q1.lower(), q2.lower()

    # 1. Year matching (e.g. 2024 vs 2025)
    curr_years = set(re.findall(r'\b(20[12]\d)\b', q1_lower))
    match_years = set(re.findall(r'\b(20[12]\d)\b', q2_lower))
    if curr_years != match_years:
        return False

    # 2. Month matching (e.g. january vs february)
    months = [
        'january', 'february', 'march', 'april', 'may', 'june', 
        'july', 'august', 'september', 'october', 'november', 'december',
        'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'
    ]
    q1_months = {m for m in months if re.search(r'\b' + m + r'\b', q1_lower)}
    q2_months = {m for m in months if re.search(r'\b' + m + r'\b', q2_lower)}
    if q1_months != q2_months:
        return False

    # 3. Digit month matching (e.g. 05/2025 vs 06/2025)
    q1_digit_months = set(re.findall(r'\b(0[1-9]|1[0-2])[-/](20[12]\d)\b', q1_lower) + re.findall(r'\b(20[12]\d)[-/](0[1-9]|1[0-2])\b', q1_lower))
    q2_digit_months = set(re.findall(r'\b(0[1-9]|1[0-2])[-/](20[12]\d)\b', q2_lower) + re.findall(r'\b(20[12]\d)[-/](0[1-9]|1[0-2])\b', q2_lower))
    if q1_digit_months != q2_digit_months:
        return False

    # 4. Trend/chart request mismatch
    trend_words = ["trend", "daily", "weekly", "chart", "graph", "plot", "map", "viz", "visualization"]
    q1_is_trend = any(w in q1_lower for w in trend_words)
    q2_is_trend = any(w in q2_lower for w in trend_words)
    if q1_is_trend != q2_is_trend:
        return False

    # 5. Dimension/Platform mismatch (essential to prevent Nykaa vs Myntra mixups)
    platforms = ["shopify", "nykaa", "myntra", "zoho", "unicomm", "amazon", "offline", "b2b"]
    q1_platforms = {p for p in platforms if p in q1_lower}
    q2_platforms = {p for p in platforms if p in q2_lower}
    if q1_platforms != q2_platforms:
        return False

    # 6. Category mismatch (Skincare vs Makeup vs Haircare)
    categories = ["skincare", "makeup", "haircare"]
    q1_categories = {c for c in categories if c in q1_lower}
    q2_categories = {c for c in categories if c in q2_lower}
    if q1_categories != q2_categories:
        return False

    # 7. Core metric mismatch (Sales/Revenue vs Units/Volume)
    revenue_words = ["sales", "revenue", "subtotal", "price", "spend", "value", "amount", "earning", "profit"]
    units_words = ["unit", "qty", "quantity", "volume", "order", "count", "inventory", "stock"]
    q1_has_revenue = any(w in q1_lower for w in revenue_words)
    q2_has_revenue = any(w in q2_lower for w in revenue_words)
    q1_has_units = any(w in q1_lower for w in units_words)
    q2_has_units = any(w in q2_lower for w in units_words)
    
    if q1_has_revenue != q2_has_revenue or q1_has_units != q2_has_units:
        return False

    return True

