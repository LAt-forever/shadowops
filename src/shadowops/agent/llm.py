"""Bounded provider-neutral LLM boundary and OpenAI Responses adapter."""

import json
import re
import time
from collections.abc import Callable, Mapping
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from shadowops.agent.contracts import PlannerRequestV1, ProviderResponseV1

_DSN = re.compile(r"(?i)\b(?:postgres(?:ql)?|redis)://[^\s\"']+")
_SECRET = re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\b(\s*[:=]\s*)([^\s,;\"']+)")
_SECRET_KEY = re.compile(r"(?i)(?:^|[_-])(?:api[_-]?key|token|password|secret)(?:$|[_-])")
_OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")


class LLMRequestV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    phase: Literal["PLANNER", "REPORTER"]
    instructions: str = Field(min_length=1, max_length=16_384)
    input_payload: dict[str, Any]
    output_schema_name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    output_schema: dict[str, Any]
    max_output_tokens: int = Field(ge=128, le=16_384)


class LLMProvider(Protocol):
    provider_name: str
    model_name: str

    def invoke(self, request: LLMRequestV1) -> ProviderResponseV1: ...


class LLMProviderError(RuntimeError):
    def __init__(self, code: str, detail: str, *, retryable: bool = False) -> None:
        self.code = code
        self.detail = detail[:500]
        self.retryable = retryable
        super().__init__(self.detail)


class OpenAIResponsesProvider:
    """Small live adapter; credentials and model are trusted worker configuration."""

    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 30.0,
        max_attempts: int = 2,
        client: httpx.Client | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if not api_key or not model or max_attempts not in {1, 2}:
            raise ValueError("OpenAI live mode requires key, model, and a bounded attempt budget")
        self.model_name = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._max_attempts = max_attempts
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._monotonic = monotonic or time.monotonic

    def invoke(self, request: LLMRequestV1) -> ProviderResponseV1:
        started = self._monotonic()
        safe_input = _redact_sensitive(request.input_payload)
        body = {
            "model": self.model_name,
            "instructions": request.instructions,
            "input": json.dumps(
                safe_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
            "max_output_tokens": request.max_output_tokens,
            "store": False,
            "parallel_tool_calls": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": request.output_schema_name,
                    "strict": True,
                    "schema": request.output_schema,
                }
            },
        }
        for attempt in range(self._max_attempts):
            try:
                response = self._client.post(
                    f"{self._base_url}/responses",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=body,
                )
            except httpx.TimeoutException as exc:
                if attempt + 1 < self._max_attempts:
                    continue
                raise LLMProviderError(
                    "LLM_TIMEOUT", "Provider request timed out", retryable=True
                ) from exc
            except httpx.RequestError as exc:
                if attempt + 1 < self._max_attempts:
                    continue
                raise LLMProviderError(
                    "LLM_PROVIDER_UNAVAILABLE",
                    "Provider request failed after retry",
                    retryable=True,
                ) from exc
            if response.status_code == 429:
                if attempt + 1 < self._max_attempts:
                    continue
                raise LLMProviderError(
                    "LLM_RATE_LIMITED", "Provider rate limit persisted after retry", retryable=True
                )
            if response.status_code >= 500:
                if attempt + 1 < self._max_attempts:
                    continue
                raise LLMProviderError(
                    "LLM_PROVIDER_UNAVAILABLE",
                    f"Provider returned HTTP {response.status_code}",
                    retryable=True,
                )
            if response.status_code >= 400:
                raise LLMProviderError(
                    "LLM_PROVIDER_REJECTED", f"Provider returned HTTP {response.status_code}"
                )
            try:
                payload = response.json()
                text = _response_output_text(payload)
                usage_value = payload.get("usage")
                usage = usage_value if isinstance(usage_value, dict) else {}
            except (ValueError, TypeError) as exc:
                raise LLMProviderError(
                    "LLM_RESPONSE_INVALID", "Provider response omitted valid output_text"
                ) from exc
            return ProviderResponseV1(
                text=text,
                response_id=str(payload.get("id")) if payload.get("id") else None,
                input_tokens=_optional_int(usage.get("input_tokens")),
                output_tokens=_optional_int(usage.get("output_tokens")),
                latency_ms=max(0, round((self._monotonic() - started) * 1000)),
            )
        raise AssertionError("bounded provider attempt loop did not return")


class RecordedLLMProvider:
    provider_name = "recorded"

    def __init__(self, model: str, responses: Mapping[str, str]) -> None:
        self.model_name = model
        self._responses = dict(responses)

    def invoke(self, request: LLMRequestV1) -> ProviderResponseV1:
        key = f"{request.phase}:{request.input_payload.get('input_hash', '')}"
        text = self._responses.get(key) or self._responses.get(request.phase)
        if text is None:
            raise LLMProviderError("LLM_RECORDING_MISSING", "No matching recorded response")
        return ProviderResponseV1(text=text)


class FakeLLMProvider:
    provider_name = "fake"
    model_name = "shadowops-reference-reporter-v1"

    def invoke(self, request: LLMRequestV1) -> ProviderResponseV1:
        if request.phase != "REPORTER":
            raise LLMProviderError("LLM_PHASE_UNSUPPORTED", "Fake LLM supports Reporter only")
        observations = request.input_payload.get("observations", [])
        evidence_ids = sorted(
            {
                evidence_id
                for observation in observations
                if isinstance(observation, dict)
                for evidence_id in observation.get("evidence_ids", [])
                if isinstance(evidence_id, str)
            }
        )
        static_risk = "INFO"
        if observations and isinstance(observations[0], dict):
            candidate = observations[0].get("data", {}).get("risk_level")
            if candidate in {"INFO", "LOW", "MEDIUM", "HIGH"}:
                static_risk = candidate
        facts = []
        if evidence_ids:
            facts.append(
                {
                    "statement": "The report is grounded in persisted audit evidence.",
                    "evidence_ids": [evidence_ids[0]],
                }
            )
        draft = {
            "schema_version": "1.0",
            "summary": "Deterministic evidence was evaluated within the ShadowOps boundary.",
            "assessed_risk": static_risk,
            "facts": facts,
            "recommendations": ["Review deterministic policy reasons before approval."],
            "unknowns": ["Shadow observations do not establish production data or lock behavior."],
        }
        return ProviderResponseV1(
            text=json.dumps(draft, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )


class PlannerLLMAdapter:
    """Adapts the generic LLM boundary to the existing planner provider contract."""

    instructions = (
        "Return only the requested audit plan JSON. Use only allowlisted capabilities and "
        "evidence references present in the input. Never emit SQL, shell, Docker, network, "
        "credential, approval, deployment, or host-path instructions."
    )

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider
        self.provider_name = provider.provider_name
        self.model_name = provider.model_name

    def invoke(self, request: PlannerRequestV1) -> ProviderResponseV1:
        from shadowops.agent.contracts import AuditPlanV1

        return self._provider.invoke(
            LLMRequestV1(
                phase="PLANNER",
                instructions=self.instructions,
                input_payload=request.model_dump(mode="json"),
                output_schema_name="AuditPlanV1",
                output_schema=AuditPlanV1.model_json_schema(),
                max_output_tokens=4_096,
            )
        )


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _response_output_text(payload: object) -> str:
    """Read either an SDK convenience field or the REST response output blocks."""
    if not isinstance(payload, dict):
        raise TypeError("provider response is not an object")
    convenience = payload.get("output_text")
    if isinstance(convenience, str):
        return convenience

    parts: list[str] = []
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "output_text"
                    and isinstance(block.get("text"), str)
                ):
                    parts.append(block["text"])
    if not parts:
        raise TypeError("provider response has no output text")
    return "".join(parts)


def _redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if isinstance(key, str) and _SECRET_KEY.search(key)
            else _redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redact_sensitive(item) for item in value]
    if isinstance(value, str):
        redacted = _DSN.sub("[REDACTED_DSN]", value)
        redacted = _SECRET.sub(r"\1\2[REDACTED]", redacted)
        return _OPENAI_KEY.sub("[REDACTED_API_KEY]", redacted)
    return value
