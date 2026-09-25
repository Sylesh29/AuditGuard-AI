"""Thin wrapper around the Anthropic SDK.

The LLM is optional: every caller has a deterministic fallback, so an outage, a missing key,
or a refusal degrades the prose in the report, never the findings.
"""
from __future__ import annotations

import logging
from typing import Protocol, TypeVar

import anthropic
import pydantic
from pydantic import BaseModel

from settings import Settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class StructuredLLM(Protocol):
    enabled: bool
    model: str

    async def parse(self, *, system: str, user: str, schema: type[T], max_tokens: int) -> T | None:
        ...


class AnthropicLLM:
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
