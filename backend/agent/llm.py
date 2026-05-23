"""
LLM routing for the Data Visualization Copilot.
Uses LiteLLM for provider-agnostic calls.

Model assignment by task:
  routing  → groq/8B (fast, cheap, intent classification)
  sql      → groq/70B → groq/8B → gemini-2.5-flash → gemini-flash-latest
  analysis → groq/70B → groq/8B → gemini-2.5-flash → gemini-flash-latest
  general  → same as sql

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


def _inject_keys() -> None:
    if settings.groq_api_key:
        os.environ["GROQ_API_KEY"] = settings.groq_api_key
    if settings.anthropic_api_key:
        os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
    if settings.openai_api_key:
        os.environ["OPENAI_API_KEY"] = settings.openai_api_key
    if settings.gemini_api_key:
        os.environ["GEMINI_API_KEY"] = settings.gemini_api_key
        os.environ["GOOGLE_API_KEY"] = settings.gemini_api_key
    if settings.mistral_api_key:
        os.environ["MISTRAL_API_KEY"] = settings.mistral_api_key
    if settings.openrouter_api_key:
        os.environ["OPENROUTER_API_KEY"] = settings.openrouter_api_key
    if settings.deepseek_api_key:
        os.environ["DEEPSEEK_API_KEY"] = settings.deepseek_api_key
    if settings.cohere_api_key:
        os.environ["COHERE_API_KEY"] = settings.cohere_api_key
    if settings.zhipu_api_key:
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
    if "openrouter" in model:
        return settings.openrouter_api_key or None
    if "deepseek" in model:
        return settings.deepseek_api_key or None
    if "cohere" in model or "command" in model:
        return settings.cohere_api_key or None
    if "zhipu" in model or "glm" in model:
        return settings.zhipu_api_key or None
    return None


def _build_fallback_chain(primary: str, task: str) -> list[str]:
    """
    Build the ordered fallback model list for a given task.

    routing  → fast 8B only (+ gemini if available)
    sql      → smart 70B → fast 8B → gemini-2.5-flash → gemini-flash-latest
    analysis → smart 70B → fast 8B → gemini-2.5-flash → gemini-flash-latest
    general  → same as sql
    """
    chain: list[str] = [primary]

    if task == "routing":
        # Routing only needs a fast model; add Gemini 1.5 Flash as last resort (1500 req/day)
        if settings.gemini_api_key and "gemini" not in primary:
            chain.append("gemini/gemini-1.5-flash")
        return chain

    # For sql / analysis / general:
    # Add Groq 8B as second option (handles simpler queries)
    fast = settings.llm_fast_model
    if fast not in chain:
        chain.append(fast)

    # Add Gemini models as further fallbacks
    # gemini-1.5-flash: 1,500 req/day free — much more generous than 2.5-flash (20/day)
    # gemini-1.5-pro: 50 req/day free — higher quality, good for complex SQL
    if settings.gemini_api_key:
        if "gemini/gemini-1.5-flash" not in chain:
            chain.append("gemini/gemini-1.5-flash")
        if "gemini/gemini-1.5-pro" not in chain:
            chain.append("gemini/gemini-1.5-pro")

    # Mistral / DeepSeek as last resort if keys exist
    if settings.deepseek_api_key and "deepseek" not in primary:
        chain.append("deepseek/deepseek-coder")
    if settings.mistral_api_key and "mistral" not in primary:
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
    # Force cold temperature for deterministic and fast responses
    temperature = 0.0

    if model is None:
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
