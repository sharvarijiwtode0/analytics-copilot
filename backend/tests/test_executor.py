"""Tests for executor.py — error pattern detection."""
import pytest
from backend.agent.nodes.executor import _is_fixable


class TestFixableErrors:
    def test_unknown_identifier(self):
        assert _is_fixable("unknown_identifier: foo_bar") is True

    def test_missing_columns(self):
        assert _is_fixable("missing columns: revenue") is True

    def test_no_such_column(self):
        assert _is_fixable("no such column: invalid_col") is True

    def test_syntax_error(self):
        assert _is_fixable("syntax error: ) near 'FROM'") is True

    def test_unknown_function(self):
        assert _is_fixable("unknown function: lag") is True

    def test_non_fixable_type_mismatch(self):
        assert _is_fixable("Type mismatch: expected Int64, got String") is False

    def test_non_fixable_timeout(self):
        assert _is_fixable("Timeout exceeded: query took 120s") is False

    def test_non_fixable_table_not_found(self):
        assert _is_fixable("Table nonexistent_table doesn't exist") is False

    def test_case_insensitive(self):
        assert _is_fixable("UNKNOWN_IDENTIFIER: foo") is True
        assert _is_fixable("SYNTAX ERROR: near SELECT") is True
