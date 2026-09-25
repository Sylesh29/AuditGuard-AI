"""The real provider wrappers and SDKs (Anthropic, Groq), with HTTP replaced by a mock transport."""
import asyncio
import json

import anthropic
import httpx
import httpx2
import pytest

from conftest import with_settings
from llm import AnthropicLLM, GroqLLM, create_llm
from models import ExecutiveSummary
from settings import Settings


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


# --- Groq -------------------------------------------------------------------------------


def completion(content: str, finish_reason: str = "stop") -> dict:
    return {
        "id": "chatcmpl-test", "object": "chat.completion", "created": 0,
        "model": "llama-3.3-70b-versatile",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def make_groq(settings, handler, retries=0):
    seen: list[httpx.Request] = []

    def record(request):
        seen.append(request)
        return handler(request)

    configured = with_settings(settings, llm_provider="groq", llm_enabled=True,
                               groq_api_key="gsk-test", llm_model="llama-3.3-70b-versatile",
                               llm_max_retries=retries)
    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return GroqLLM(configured, http_client=client), seen


def test_groq_request_shape_and_parsed_response(settings):
    body = json.dumps({"summary": "AuditGuard AI found 3 issues."})
    llm, seen = make_groq(settings, lambda r: httpx.Response(200, json=completion(body)))
    assert call(llm) == ExecutiveSummary(summary="AuditGuard AI found 3 issues.")

    sent = json.loads(seen[0].content)
    assert seen[0].url.path == "/openai/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer gsk-test"
    assert sent["model"] == "llama-3.3-70b-versatile"
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["temperature"] == 0 and sent["max_completion_tokens"] == 500
    system, user = sent["messages"]
    assert system["role"] == "system" and '"summary"' in system["content"]
    assert user == {"role": "user", "content": "<audit_data>{}</audit_data>"}


@pytest.mark.parametrize("status", [400, 401, 404, 429, 500, 503])
def test_groq_http_errors_fall_back(settings, status):
    llm, _ = make_groq(settings, lambda r: httpx.Response(status, json={"error": {"message": "x"}}))
    assert call(llm) is None


@pytest.mark.parametrize("content,finish", [
    ('{"summary": "cut', "length"),          # truncated
    ('{"summary": "ok"}', "length"),         # valid JSON but stopped early
    ("not json at all", "stop"),
    ('{"wrong_field": "x"}', "stop"),
    ("", "stop"),
])
def test_groq_bad_output_falls_back(settings, content, finish):
    llm, _ = make_groq(settings, lambda r: httpx.Response(200, json=completion(content, finish)))
    assert call(llm) is None


def test_groq_connection_error_falls_back(settings):
    def boom(request):
        raise httpx.ConnectError("unreachable", request=request)
    llm, _ = make_groq(settings, boom)
    assert call(llm) is None


def test_groq_retries_rate_limits(settings):
    responses = iter([
        httpx.Response(429, json={"error": {"message": "slow down"}}, headers={"retry-after": "0"}),
        httpx.Response(200, json=completion(json.dumps({"summary": "ok 1"}))),
    ])
    llm, seen = make_groq(settings, lambda r: next(responses), retries=2)
    assert call(llm) == ExecutiveSummary(summary="ok 1")
    assert len(seen) == 2


@pytest.mark.parametrize("env,provider,model,enabled", [
    ({}, "anthropic", "claude-sonnet-4-6", False),
    ({"ANTHROPIC_API_KEY": "a"}, "anthropic", "claude-sonnet-4-6", True),
    ({"GROQ_API_KEY": "g"}, "groq", "llama-3.3-70b-versatile", True),
    ({"GROQ_API_KEY": "g", "ANTHROPIC_API_KEY": "a"}, "anthropic", "claude-sonnet-4-6", True),
    ({"GROQ_API_KEY": "g", "ANTHROPIC_API_KEY": "a", "AUDITGUARD_LLM_PROVIDER": "groq"},
     "groq", "llama-3.3-70b-versatile", True),
    ({"AUDITGUARD_LLM_PROVIDER": "groq"}, "groq", "llama-3.3-70b-versatile", False),
    ({"GROQ_API_KEY": "g", "AUDITGUARD_LLM_MODEL": "openai/gpt-oss-120b"},
     "groq", "openai/gpt-oss-120b", True),
])
def test_provider_selection_from_environment(monkeypatch, env, provider, model, enabled):
    for name in ("ANTHROPIC_API_KEY", "GROQ_API_KEY", "AUDITGUARD_LLM_PROVIDER",
                 "AUDITGUARD_LLM_MODEL", "AUDITGUARD_LLM_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    configured = Settings.from_env()
    llm = create_llm(configured)
    assert (llm.provider, llm.model, llm.enabled) == (provider, model, enabled)


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("AUDITGUARD_LLM_PROVIDER", "openai")
    with pytest.raises(ValueError, match="AUDITGUARD_LLM_PROVIDER"):
        Settings.from_env()
