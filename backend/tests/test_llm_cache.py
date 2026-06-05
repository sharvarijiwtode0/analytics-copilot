"""Tests for LLM cache — key normalization and TTL."""
import pytest
from backend.services.llm_cache import LLMCache


class TestLLMCache:
    def test_store_and_retrieve(self):
        cache = LLMCache()
        cache.set("show sales", "ds:test", {"sql": "SELECT 1", "row_count": 10})
        result = cache.get("show sales", "ds:test")
        assert result is not None
        assert result["sql"] == "SELECT 1"

    def test_case_insensitive_key(self):
        cache = LLMCache()
        cache.set("Show Sales", "ds:test", {"sql": "SELECT 1"})
        result = cache.get("show sales", "ds:test")
        assert result is not None

    def test_empty_result_still_stored_in_memory(self):
        # Memory cache stores all results; only Redis rejects empty row_count
        cache = LLMCache()
        cache.set("query", "ds:test", {"sql": "SELECT 1", "row_count": 0})
        result = cache.get("query", "ds:test")
        assert result is not None
        assert result["sql"] == "SELECT 1"

    def test_different_datasource_no_match(self):
        cache = LLMCache()
        cache.set("query", "ds:a", {"sql": "SELECT 1", "row_count": 5})
        result = cache.get("query", "ds:b")
        assert result is None
