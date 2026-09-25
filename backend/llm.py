"""Pluggable LLM providers (Anthropic or Groq) behind one small interface.

The LLM is optional: every caller has a deterministic fallback, so an outage, a missing key,
a truncated answer or a refusal degrades the prose in the report, never the findings. Both
providers return a validated Pydantic object or None, and never raise for API problems.
"""
from __future__ import annotations

import json
import logging
from typing import Protocol, TypeVar

import anthropic
import groq
import httpx
import pydantic
from pydantic import BaseModel

from settings import Settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class StructuredLLM(Protocol):
    enabled: bool
    provider: str
    model: str

    async def parse(self, *, system: str, user: str, schema: type[T], max_tokens: int) -> T | None:
        ...


class AnthropicLLM:
    provider = "anthropic"

    def __init__(self, settings: Settings, http_client: anthropic.DefaultAsyncHttpxClient | None = None
                 ) -> None:
        self.model = settings.llm_model
        self.enabled = settings.llm_enabled
        self._client = (
            anthropic.AsyncAnthropic(
                api_key=settings.anthropic_api_key,
                timeout=settings.llm_timeout_s,
                max_retries=settings.llm_max_retries,
                http_client=http_client,
            )
            if self.enabled
            else None
        )

    async def parse(self, *, system: str, user: str, schema: type[T], max_tokens: int) -> T | None:
        if self._client is None:
            return None
        try:
            response = await self._client.messages.parse(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
            )
        except anthropic.RateLimitError:
            logger.warning("LLM rate limited after retries; using deterministic fallback")
            return None
        except anthropic.APIStatusError as exc:
            logger.warning("LLM request failed with HTTP %s; using fallback", exc.status_code)
            return None
        except anthropic.APIConnectionError:
            logger.warning("LLM unreachable; using deterministic fallback")
            return None
        except pydantic.ValidationError:
            # The SDK validates structured output eagerly; truncated (max_tokens) or refused
            # output fails here. That must degrade the prose, never fail the audit.
            logger.warning("LLM output did not match the %s schema; using fallback", schema.__name__)
            return None
        if response.stop_reason != "end_turn" or response.parsed_output is None:
            logger.warning("LLM returned no usable output (stop_reason=%s)", response.stop_reason)
            return None
        return response.parsed_output

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()


class GroqLLM:
    """Groq's chat API in JSON mode. The schema goes in the system prompt and the reply is
    validated locally against the same Pydantic model the Anthropic path uses."""

    provider = "groq"

    def __init__(self, settings: Settings, http_client: httpx.AsyncClient | None = None) -> None:
        self.model = settings.llm_model
        self.enabled = settings.llm_enabled
        self._client = (
            groq.AsyncGroq(
                api_key=settings.groq_api_key,
                timeout=settings.llm_timeout_s,
                max_retries=settings.llm_max_retries,
                http_client=http_client,
            )
            if self.enabled
            else None
        )

    async def parse(self, *, system: str, user: str, schema: type[T], max_tokens: int) -> T | None:
        if self._client is None:
            return None
        instructions = (
            f"{system}\n\nRespond with one JSON object that matches this JSON Schema, and nothing "
            f"else:\n{json.dumps(schema.model_json_schema())}"
        )
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": instructions},
                          {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                temperature=0,
                max_completion_tokens=max_tokens,
            )
            choice = response.choices[0]
            if choice.finish_reason != "stop" or not choice.message.content:
                logger.warning("LLM returned no usable output (finish_reason=%s)", choice.finish_reason)
                return None
            return schema.model_validate_json(choice.message.content)
        except groq.RateLimitError:
            logger.warning("LLM rate limited after retries; using deterministic fallback")
        except groq.APIStatusError as exc:
            logger.warning("LLM request failed with HTTP %s; using fallback", exc.status_code)
        except groq.APIConnectionError:
            logger.warning("LLM unreachable; using deterministic fallback")
        except pydantic.ValidationError:
            logger.warning("LLM output did not match the %s schema; using fallback", schema.__name__)
        return None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()


def create_llm(settings: Settings) -> AnthropicLLM | GroqLLM:
    return GroqLLM(settings) if settings.llm_provider == "groq" else AnthropicLLM(settings)
