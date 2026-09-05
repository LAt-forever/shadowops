import json

import httpx
import pytest

from shadowops.agent.llm import (
    LLMProviderError,
    LLMRequestV1,
    OpenAIResponsesProvider,
    RecordedLLMProvider,
)


def _request() -> LLMRequestV1:
    return LLMRequestV1(
        phase="REPORTER",
        instructions="Return grounded JSON.",
        input_payload={"input_hash": "a" * 64},
        output_schema_name="Report",
        output_schema={"type": "object", "additionalProperties": False},
        max_output_tokens=512,
    )


def test_openai_responses_adapter_uses_strict_stateless_output_and_usage() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_123",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"ok":true}'}],
                    }
                ],
                "usage": {"input_tokens": 12, "output_tokens": 4},
            },
        )

    ticks = iter((10.0, 10.25))
    provider = OpenAIResponsesProvider(
        api_key="test-key",
        model="configured-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        monotonic=lambda: next(ticks),
    )

    response = provider.invoke(_request())

    assert response.response_id == "resp_123"
    assert response.input_tokens == 12
    assert response.output_tokens == 4
    assert response.latency_ms == 250
    expected_input = json.dumps({"input_hash": "a" * 64}, separators=(",", ":"))
    assert seen == [
        {
            "model": "configured-model",
            "instructions": "Return grounded JSON.",
            "input": expected_input,
            "max_output_tokens": 512,
            "store": False,
            "parallel_tool_calls": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "Report",
                    "strict": True,
                    "schema": {"type": "object", "additionalProperties": False},
                }
            },
        }
    ]


def test_openai_responses_adapter_retries_one_rate_limit() -> None:
    statuses = iter((429, 200))

    def handler(_: httpx.Request) -> httpx.Response:
        status = next(statuses)
        return httpx.Response(status, json={"output_text": "{}"})

    provider = OpenAIResponsesProvider(
        api_key="test-key",
        model="configured-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        monotonic=lambda: 0.0,
    )

    assert provider.invoke(_request()).text == "{}"


def test_provider_failure_is_stable_after_bounded_retry() -> None:
    provider = OpenAIResponsesProvider(
        api_key="test-key",
        model="configured-model",
        client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(429, json={}))),
        monotonic=lambda: 0.0,
    )

    with pytest.raises(LLMProviderError) as error:
        provider.invoke(_request())

    assert error.value.code == "LLM_RATE_LIMITED"
    assert error.value.retryable is True


def test_provider_timeout_is_stable_after_bounded_retry() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider = OpenAIResponsesProvider(
        api_key="test-key",
        model="configured-model",
        client=httpx.Client(transport=httpx.MockTransport(timeout)),
        monotonic=lambda: 0.0,
    )

    with pytest.raises(LLMProviderError) as error:
        provider.invoke(_request())

    assert error.value.code == "LLM_TIMEOUT"
    assert error.value.retryable is True


def test_provider_network_failure_is_stable_after_bounded_retry() -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unavailable", request=request)

    provider = OpenAIResponsesProvider(
        api_key="test-key",
        model="configured-model",
        client=httpx.Client(transport=httpx.MockTransport(unavailable)),
        monotonic=lambda: 0.0,
    )

    with pytest.raises(LLMProviderError) as error:
        provider.invoke(_request())

    assert error.value.code == "LLM_PROVIDER_UNAVAILABLE"
    assert error.value.retryable is True


def test_recorded_provider_replays_phase_or_exact_input_hash() -> None:
    request = _request()
    provider = RecordedLLMProvider(
        "recorded-model",
        {"REPORTER": "fallback", f"REPORTER:{'a' * 64}": "exact"},
    )

    assert provider.invoke(request).text == "exact"


def test_openai_responses_adapter_redacts_prompt_secrets() -> None:
    captured = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = str(json.loads(request.content)["input"])
        return httpx.Response(200, json={"output_text": "{}"})

    request = _request().model_copy(
        update={
            "input_payload": {
                "text": "password=hunter2 postgresql://user:pass@db/name sk-exampleSecret123",
                "database_password": "nested-secret",
            }
        }
    )
    provider = OpenAIResponsesProvider(
        api_key="test-key",
        model="configured-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        monotonic=lambda: 0.0,
    )

    provider.invoke(request)

    assert "hunter2" not in captured
    assert "user:pass" not in captured
    assert "sk-exampleSecret123" not in captured
    assert "nested-secret" not in captured
    assert "[REDACTED]" in captured
