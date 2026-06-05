"""Tests for connector.py — SQL safety enforcement."""
import pytest
import asyncio
from unittest.mock import patch, MagicMock


class TestSQLSafety:
    @pytest.mark.asyncio
    async def test_select_allowed(self):
        from backend.data.connector import _is_readonly_query
        assert await _is_readonly_query("SELECT * FROM sales") is True

    @pytest.mark.asyncio
    async def test_with_allowed(self):
        from backend.data.connector import _is_readonly_query
        assert await _is_readonly_query("WITH cte AS (SELECT 1) SELECT * FROM cte") is True

    @pytest.mark.asyncio
    async def test_insert_blocked(self):
        from backend.data.connector import _is_readonly_query
        assert await _is_readonly_query("INSERT INTO sales VALUES (1, 2)") is False

    @pytest.mark.asyncio
    async def test_delete_blocked(self):
        from backend.data.connector import _is_readonly_query
        assert await _is_readonly_query("DELETE FROM sales WHERE id = 1") is False

    @pytest.mark.asyncio
    async def test_update_blocked(self):
        from backend.data.connector import _is_readonly_query
        assert await _is_readonly_query("UPDATE sales SET revenue = 100") is False

    @pytest.mark.asyncio
    async def test_drop_blocked(self):
        from backend.data.connector import _is_readonly_query
        assert await _is_readonly_query("DROP TABLE sales") is False

    @pytest.mark.asyncio
    async def test_truncate_blocked(self):
        from backend.data.connector import _is_readonly_query
        assert await _is_readonly_query("TRUNCATE TABLE sales") is False

    @pytest.mark.asyncio
    async def test_alter_blocked(self):
        from backend.data.connector import _is_readonly_query
        assert await _is_readonly_query("ALTER TABLE sales ADD COLUMN x INT") is False

    @pytest.mark.asyncio
    async def test_case_insensitive(self):
        from backend.data.connector import _is_readonly_query
        assert await _is_readonly_query("select * from sales") is True
        assert await _is_readonly_query("SELECT * FROM Sales") is True

    @pytest.mark.asyncio
    async def test_delete_in_string_literal_blocked(self):
        from backend.data.connector import _is_readonly_query
        assert await _is_readonly_query("SELECT * FROM sales WHERE name = 'delete me'") is False


class TestDatasourceRegistration:
    def test_register_and_get(self):
        from backend.data.connector import register_datasource, _datasources
        original = dict(_datasources)
        try:
            register_datasource("test_ds", "sqlite", {"path": ":memory:"})
            assert "test_ds" in _datasources
        finally:
            _datasources.clear()
            _datasources.update(original)

    def test_get_schema_empty_datasource(self):
        from backend.data.connector import get_schema
        import asyncio
        result = asyncio.run(get_schema("nonexistent"))
        assert "tables" in result
