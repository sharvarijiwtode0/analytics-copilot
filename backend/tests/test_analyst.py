"""Tests for analyst.py — stat computation and fallback insights."""
import pytest
from backend.agent.nodes.analyst import _compute_basic_stats, _generate_rule_based_fallback_insights


class TestComputeBasicStats:
    def test_empty_data(self):
        stats = _compute_basic_stats([], ["id", "revenue"])
        assert stats == {}

    def test_single_numeric_column(self):
        rows = [
            {"revenue": 100, "id": 1},
            {"revenue": 200, "id": 2},
            {"revenue": 300, "id": 3},
        ]
        stats = _compute_basic_stats(rows, ["id", "revenue"])
        assert "id" not in stats  # ID columns are skipped
        assert stats["revenue"]["min"] == 100
        assert stats["revenue"]["max"] == 300
        assert stats["revenue"]["avg"] == 200
        assert stats["revenue"]["total"] == 600
        assert stats["revenue"]["count"] == 3

    def test_string_column_ignored(self):
        rows = [{"name": "Alice"}, {"name": "Bob"}]
        stats = _compute_basic_stats(rows, ["name"])
        assert stats == {}

    def test_null_values_ignored(self):
        rows = [
            {"revenue": 100},
            {"revenue": None},
            {"revenue": 300},
        ]
        stats = _compute_basic_stats(rows, ["revenue"])
        assert stats["revenue"]["count"] == 2
        assert stats["revenue"]["total"] == 400


class TestRuleBasedFallback:
    def test_returns_insights(self):
        rows = [
            {"platform": "Shopee", "revenue": 1000},
            {"platform": "Tokopedia", "revenue": 2000},
            {"platform": "Lazada", "revenue": 500},
        ]
        stats = _compute_basic_stats(rows, ["platform", "revenue"])
        result = _generate_rule_based_fallback_insights(rows, ["platform", "revenue"], stats, "show revenue by platform")
        assert "insights" in result
        assert len(result["insights"]) > 0
        assert "key_metrics" in result

    def test_row_count_always_present(self):
        rows = [{"a": 1}]
        stats = _compute_basic_stats(rows, ["a"])
        result = _generate_rule_based_fallback_insights(rows, ["a"], stats, "test")
        has_row_count = any("1" in ins and "record" in ins for ins in result["insights"])
        assert has_row_count

    def test_empty_rows(self):
        result = _generate_rule_based_fallback_insights([], [], {}, "test")
        assert "insights" in result
