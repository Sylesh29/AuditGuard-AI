"""The real AnthropicLLM wrapper and the real SDK, with HTTP replaced by a mock transport."""
import asyncio
import json

import anthropic
import httpx2
import pytest

from conftest import with_settings
from llm import AnthropicLLM
from models import ExecutiveSummary


def message(text: str, stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-sonnet-4-6",
        "content": [{"type": "text", "text": text}], "stop_reason": stop_reason,
        "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 10},
    }


def make_llm(settings, handler, retries=0) -> tuple[AnthropicLLM, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    client = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(record))
    configured = with_settings(settings, llm_enabled=True, anthropic_api_key="test-key",
                               llm_model="claude-sonnet-4-6", llm_max_retries=retries)
    return AnthropicLLM(configured, http_client=client), seen


def call(llm):
    return asyncio.run(llm.parse(system="sys", user="<audit_data>{}</audit_data>",
                                 schema=ExecutiveSummary, max_tokens=500))


def test_structured_request_and_parsed_response(settings):
    body = json.dumps({"summary": "AuditGuard AI found 3 issues."})
    llm, seen = make_llm(settings, lambda r: httpx2.Response(200, json=message(body)))
    result = call(llm)
    assert result == ExecutiveSummary(summary="AuditGuard AI found 3 issues.")

    sent = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/messages"
    assert sent["model"] == "claude-sonnet-4-6" and sent["max_tokens"] == 500
    assert sent["system"] == "sys"
    assert sent["messages"] == [{"role": "user", "content": "<audit_data>{}</audit_data>"}]
    schema = sent["output_config"]["format"]["schema"]
    assert schema["properties"]["summary"]["type"] == "string"
    assert seen[0].headers["x-api-key"] == "test-key"


@pytest.mark.parametrize("status", [400, 401, 404, 429, 500, 529])
def test_http_errors_fall_back(settings, status):
    llm, _ = make_llm(settings, lambda r: httpx2.Response(status, json={
        "type": "error", "error": {"type": "api_error", "message": "nope"}}))
    assert call(llm) is None


def test_retries_transient_errors(settings):
    responses = iter([
        httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error",
                                                               "message": "busy"}},
                        headers={"retry-after-ms": "1"}),
        httpx2.Response(200, json=message(json.dumps({"summary": "ok 1"}))),
    ])
    llm, seen = make_llm(settings, lambda r: next(responses), retries=2)
    assert call(llm) == ExecutiveSummary(summary="ok 1")
    assert len(seen) == 2


def test_connection_errors_fall_back(settings):
    def boom(request):
        raise httpx2.ConnectError("unreachable", request=request)
    llm, _ = make_llm(settings, boom)
    assert call(llm) is None


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
def test_incomplete_or_refused_output_falls_back(settings, stop_reason):
    llm, _ = make_llm(settings, lambda r: httpx2.Response(
        200, json=message('{"summary": "partial', stop_reason)))
    assert call(llm) is None


def test_disabled_llm_makes_no_requests(settings):
    llm = AnthropicLLM(settings)
    assert llm.enabled is False
    assert call(llm) is None


@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal"])
def test_valid_json_with_abnormal_stop_is_not_trusted(settings, stop_reason):
    body = json.dumps({"summary": "Looks complete but was cut off."})
    llm, _ = make_llm(settings, lambda r: httpx2.Response(200, json=message(body, stop_reason)))
    assert call(llm) is None
