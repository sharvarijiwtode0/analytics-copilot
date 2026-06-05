"""
LLM routing for the Data Visualization Copilot.
Uses LiteLLM for provider-agnostic calls.

Model assignment by task (from .env):
  routing  → llm_fast_model (zhipu/glm-5-turbo) → openrouter fallback
  sql      → llm_smart_model (zhipu/glm-5-turbo) → openrouter/llama-3.3-70b → gemini fallback
  analysis → openrouter/llama-3.3-70b → zhipu → gemini fallback
  general  → same as sql

Timeouts (fail-fast for snappy failover):
  routing: 8s | sql: 20s | analysis: 15s | general: 15s

On rate limit: retries same model once (0.5s), then falls to next model.
For non-sql tasks: returns stub on total failure instead of raising.
"""
from __future__ import annotations
import asyncio
import os
import time
from dataclasses import dataclass

import litellm
import structlog

from backend.config import settings

log = structlog.get_logger(__name__)
litellm.set_verbose = False


def _is_valid_key(key: str | None) -> bool:
    if not key:
        return False
    key_strip = key.strip()
    if not key_strip or "*" in key_strip or "placeholder" in key_strip.lower() or "your_" in key_strip.lower():
        return False
    return True


def _inject_keys() -> None:
    if _is_valid_key(settings.groq_api_key):
        os.environ["GROQ_API_KEY"] = settings.groq_api_key
    if _is_valid_key(settings.anthropic_api_key):
        os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
    if _is_valid_key(settings.openai_api_key):
        os.environ["OPENAI_API_KEY"] = settings.openai_api_key
    if _is_valid_key(settings.gemini_api_key):
        os.environ["GEMINI_API_KEY"] = settings.gemini_api_key
        os.environ["GOOGLE_API_KEY"] = settings.gemini_api_key
    if _is_valid_key(settings.mistral_api_key):
        os.environ["MISTRAL_API_KEY"] = settings.mistral_api_key
    if _is_valid_key(settings.openrouter_api_key):
        os.environ["OPENROUTER_API_KEY"] = settings.openrouter_api_key
    if _is_valid_key(settings.deepseek_api_key):
        os.environ["DEEPSEEK_API_KEY"] = settings.deepseek_api_key
    if _is_valid_key(settings.cohere_api_key):
        os.environ["COHERE_API_KEY"] = settings.cohere_api_key
    if _is_valid_key(settings.zhipu_api_key):
        os.environ["ZHIPU_API_KEY"] = settings.zhipu_api_key


_inject_keys()


@dataclass
class LLMResponse:
    content: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


def _get_key(model: str) -> str | None:
    if "openrouter" in model:
        return settings.openrouter_api_key or None
    if "groq" in model:
        return settings.groq_api_key or None
    if "claude" in model or "anthropic" in model:
        return settings.anthropic_api_key or None
    if "gpt" in model or "openai" in model:
        return settings.openai_api_key or None
    if "gemini" in model:
        return settings.gemini_api_key or None
    if "mistral" in model:
        return settings.mistral_api_key or None
    if "deepseek" in model:
        return settings.deepseek_api_key or None
    if "cohere" in model or "command" in model:
        return settings.cohere_api_key or None
    if "zhipu" in model or "glm" in model:
        return settings.zhipu_api_key or None
    return None


def _build_fallback_chain(primary: str, task: str) -> list[str]:
    """
    Build the ordered fallback model list for a given task,
    filtering to only include models with active API keys to prevent error delays.
    """
    chain: list[str] = [primary]

    # 1. Zhipu GLM fallback (if active key is present and not primary)
    if _is_valid_key(settings.zhipu_api_key):
        if "zhipu/glm-5-turbo" not in chain:
            chain.append("zhipu/glm-5-turbo")

    # 2. OpenRouter fallback (uses llama-3.3-70b-instruct or gemini-2.5-flash)
    if _is_valid_key(settings.openrouter_api_key):
        if task == "routing":
            if "openrouter/google/gemini-2.5-flash" not in chain:
                chain.append("openrouter/google/gemini-2.5-flash")
        else:
            if "openrouter/meta-llama/llama-3.3-70b-instruct" not in chain:
                chain.append("openrouter/meta-llama/llama-3.3-70b-instruct")
            if "openrouter/google/gemini-2.5-flash" not in chain:
                chain.append("openrouter/google/gemini-2.5-flash")

    # 3. Gemini fallback (if active key is present)
    if _is_valid_key(settings.gemini_api_key):
        if "gemini/gemini-1.5-flash" not in chain:
            chain.append("gemini/gemini-1.5-flash")
        if task != "routing" and "gemini/gemini-1.5-pro" not in chain:
            chain.append("gemini/gemini-1.5-pro")

    # 4. Groq fallback (if active key is present)
    if _is_valid_key(settings.groq_api_key):
        if task == "routing":
            if "groq/llama-3.1-8b-instant" not in chain:
                chain.append("groq/llama-3.1-8b-instant")
        else:
            if "groq/llama-3.3-70b-versatile" not in chain:
                chain.append("groq/llama-3.3-70b-versatile")
            if "groq/llama-3.1-8b-instant" not in chain:
                chain.append("groq/llama-3.1-8b-instant")

    # 5. DeepSeek fallback (if active key is present)
    if _is_valid_key(settings.deepseek_api_key):
        if "deepseek/deepseek-coder" not in chain:
            chain.append("deepseek/deepseek-coder")

    # 6. Mistral fallback (if active key is present)
    if _is_valid_key(settings.mistral_api_key):
        if "mistral/mistral-large-latest" not in chain:
            chain.append("mistral/mistral-large-latest")

    return chain


async def call_llm(
    messages: list[dict],
    model: str | None = None,
    max_tokens: int = 2000,
    temperature: float = 0.1,
    task: str = "general",
) -> LLMResponse:
    """
    Single LLM call with automatic multi-model fallback.

    task options:
      "routing"  — fast 8B model (intent/schema selection)
      "sql"      — smart 70B model (SQL generation) — raises on total failure
      "analysis" — smart 70B model (insights) — returns stub on total failure
      "general"  — same as sql
    """
    # Differential temperature by task:
    # - routing: 0.0 (deterministic classification)
    # - sql: 0.0 (exact query generation)
    # - analysis: 0.2 (some creativity for narrative insights)
    # - general: 0.1 (natural but consistent)
    _TASK_TEMPERATURES = {
        "routing": 0.0,
        "sql": 0.0,
        "analysis": 0.2,
        "general": 0.1,
    }
    temperature = _TASK_TEMPERATURES.get(task, temperature)

    if model is None:
        if task == "analysis" and _is_valid_key(settings.openrouter_api_key):
            model = "openrouter/meta-llama/llama-3.3-70b-instruct"
        else:
            model = settings.llm_fast_model if task == "routing" else settings.llm_smart_model

    models_to_try = _build_fallback_chain(model, task)
    last_error: Exception | None = None

    for m in models_to_try:
        # Allow one retry on rate limit before moving to next model
        for attempt in range(2):
            try:
                t0 = time.perf_counter()
                # For zhipu/glm models, use openai-compatible endpoint via Z.ai
                if "zhipu" in m or "glm" in m:
                    actual_model = m.split("/", 1)[-1]  # strip "zhipu/" prefix
                    kwargs: dict = {
                        "model": f"openai/{actual_model}",
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                        "api_key": settings.zhipu_api_key,
                        "api_base": settings.zhipu_base_url,
                    }
                else:
                    kwargs: dict = {
                        "model": m,
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                    }
                    api_key = _get_key(m)
                    if api_key:
                        kwargs["api_key"] = api_key

                # Tight timeouts per task — fail fast, failover to next model immediately.
                # Zhipu/GLM can spike to 15s+ under load; we don't wait forever.
                task_timeouts = {
                    "routing": 8.0,    # intent/schema — must be snappy
                    "sql": 20.0,       # SQL generation — more tokens needed
                    "analysis": 15.0,  # insight generation
                    "general": 15.0,
                }
                kwargs["timeout"] = task_timeouts.get(task, 15.0)
                resp = await litellm.acompletion(**kwargs)
                latency_ms = int((time.perf_counter() - t0) * 1000)
                usage = resp.usage

                log.info("llm.call", model=m, task=task, latency_ms=latency_ms,
                         tokens=getattr(usage, "total_tokens", 0))

                return LLMResponse(
                    content=resp.choices[0].message.content or "",
                    model=m,
                    input_tokens=getattr(usage, "prompt_tokens", 0),
                    output_tokens=getattr(usage, "completion_tokens", 0),
                    latency_ms=latency_ms,
                )
            except Exception as exc:
                last_error = exc
                err_str = str(exc).lower()
                if "rate_limit" in err_str or "rate limit" in err_str or "429" in err_str:
                    if attempt == 0:
                        log.warning("llm.rate_limited", model=m, wait_seconds=0.5)
                        await asyncio.sleep(0.5)
                        continue  # retry same model once
                log.warning("llm.failed", model=m, attempt=attempt, error=str(exc)[:120])
                break  # move to next model
    # SQL tasks must succeed — callers handle the exception
    if task == "sql":
        raise RuntimeError(f"All LLM models failed for SQL generation. Last error: {last_error}")

    # For routing/analysis/general — return a stub so the pipeline doesn't crash
    log.warning("llm.all_failed_returning_stub", task=task, last_error=str(last_error)[:120])
    return LLMResponse(content="", model="stub", input_tokens=0, output_tokens=0, latency_ms=0)
