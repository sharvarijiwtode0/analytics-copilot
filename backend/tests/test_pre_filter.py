"""Tests for pre_filter.py — greeting detection and safety checks."""
import pytest
from backend.agent.pre_filter import _detect_off_topic, _get_greeting_response, pre_classify


class TestGreetingDetection:
    def test_greeting_returns_response(self):
        result = _get_greeting_response("hello")
        assert "response" in result
        assert len(result["response"]) > 0

    def test_good_morning_greeting(self):
        result = _get_greeting_response("good morning")
        assert "response" in result
        assert len(result["response"]) > 0

    def test_off_topic_disabled(self):
        result = _detect_off_topic("what is the weather?")
        assert result is None


class TestPreFilter:
    def test_pre_classify_greeting(self):
        result = pre_classify("hello")
        assert result.get("type") == "greeting"
        assert result.get("skip_llm") is True

    def test_pre_classify_data_question(self):
        result = pre_classify("show me sales revenue by platform")
        assert result.get("type") != "greeting"
