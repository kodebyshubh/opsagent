"""LLM provider factory and a structured-output call with retry and self-repair (TASK-010).

Provider-agnostic (DEC-003): the model is asked for a JSON object matching a Pydantic schema,
the reply is parsed with Decimal floats (money safety) and validated. Transport errors are
retried with backoff; invalid JSON / schema errors get one repair round where the model sees
its own output and the validation error (self-correction).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from decimal import Decimal
from typing import Protocol, TypeVar

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from . import audit, failures
from .config import Settings, get_settings
from .models import LLMUnavailableError, StructuredOutputError

log = logging.getLogger("opsagent.llm")
T = TypeVar("T", bound=BaseModel)

TRANSPORT_BACKOFF = (4.0, 15.0, 30.0)
RATE_LIMIT_RETRIES = 8  # 429s are not failures: wait as told and try again
EXPECTED_OUTPUT_TOKENS = 1500


def _is_rate_limit(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "rate_limit" in text


class TokenThrottle:
    """Client-side tokens-per-minute limiter so free-tier limits are respected up front.

    Tracks tokens used in the last 60 s and waits before a call that would exceed the limit.
    """

    def __init__(self, tpm_limit: int) -> None:
        self.limit = tpm_limit
        self._events: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def _used(self, now: float) -> int:
        while self._events and now - self._events[0][0] > 60:
            self._events.popleft()
        return sum(t for _, t in self._events)

    def wait(self, estimated: int) -> float:
        if self.limit <= 0:
            return 0.0
        estimated = min(estimated, self.limit)
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                if self._used(now) + estimated <= self.limit or not self._events:
                    return waited
                pause = 60 - (now - self._events[0][0]) + 0.5
            if pause > 3:
                log.warning("Pacing for the provider's tokens-per-minute limit: waiting %.0fs...",
                            pause)
            time.sleep(max(0.5, pause))
            waited += max(0.5, pause)

    def record(self, tokens: int) -> None:
        if self.limit <= 0:
            return
        with self._lock:
            self._events.append((time.monotonic(), max(0, int(tokens))))


_THROTTLES: dict[tuple[str, str, int], TokenThrottle] = {}


def throttle_for(settings: Settings) -> TokenThrottle:
    key = (settings.provider, settings.model, settings.tpm_limit)
    if key not in _THROTTLES:
        _THROTTLES[key] = TokenThrottle(settings.tpm_limit)
    return _THROTTLES[key]


def _estimate_tokens(messages) -> int:
    chars = sum(len(m.content) if isinstance(m.content, str) else 0 for m in messages)
    return chars // 4 + EXPECTED_OUTPUT_TOKENS


class StructuredCall(Protocol):
    def __call__(self, schema: type[T], system: str, user: str, *, run_id: str | None,
                 node: str) -> T: ...


def get_chat_model(settings: Settings):
    settings.require_llm()
    if settings.provider == "groq":
        from langchain_groq import ChatGroq

        extra = {}
        if settings.reasoning_effort and "gpt-oss" in settings.model:
            extra["reasoning_effort"] = settings.reasoning_effort  # fewer hidden tokens
        return ChatGroq(
            model=settings.model,
            api_key=settings.groq_api_key,
            temperature=0.1,
            timeout=settings.llm_timeout,
            max_retries=0,
            model_kwargs={"response_format": {"type": "json_object"}},
            **extra,
        )
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=settings.model,
        google_api_key=settings.google_api_key,
        temperature=0.1,
        timeout=settings.llm_timeout,
        max_retries=0,
        response_mime_type="application/json",
    )


def extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in model output.")
    return json.loads(text[start : end + 1], parse_float=Decimal)


def _retry_delay(exc: Exception, default: float) -> float:
    """Honour 'try again in 7.5s' hints from rate-limit errors (capped at 60 s)."""
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", str(exc))
    if m:
        return min(60.0, float(m.group(1) or 0) * 60 + float(m.group(2)) + 1.0)
    return default


def schema_instructions(schema: type[BaseModel], system: str) -> str:
    if "OUTPUT JSON FORMAT" in system:  # prompt already carries a compact template
        return ""
    return (
        "\n\nOUTPUT FORMAT: respond with ONLY one JSON object (no prose, no markdown) that "
        "validates against this JSON Schema:\n"
        + json.dumps(schema.model_json_schema(), separators=(",", ":"))
    )


class LLMClient:
    """Callable implementing StructuredCall with the configured provider."""

    def __init__(self, settings: Settings | None = None,
                 model_factory: Callable[[Settings], object] = get_chat_model) -> None:
        self.settings = settings or get_settings()
        self._model = model_factory(self.settings)
        self._throttle = throttle_for(self.settings)

    def _invoke(self, messages, run_id: str | None, node: str, attempt: int):
        last_exc: Exception | None = None
        errors, rate_limits, tries = 0, 0, 0
        estimated = _estimate_tokens(messages)
        while True:
            tries += 1
            waited = self._throttle.wait(estimated)
            if waited and run_id:
                audit.log(run_id, "rate_limit_pacing", node, {"waited_s": round(waited, 1)})
            t0 = time.perf_counter()
            try:
                resp = self._model.invoke(messages)
                latency = int((time.perf_counter() - t0) * 1000)
                usage = getattr(resp, "usage_metadata", None) or {}
                self._throttle.record(usage.get("total_tokens") or estimated)
                if run_id:
                    audit.log(run_id, "llm_call", node, {
                        "provider": self.settings.provider, "model": self.settings.model,
                        "latency_ms": latency, "attempt": attempt, "transport_try": tries,
                        "input_tokens": usage.get("input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                    })
                return resp
            except Exception as exc:  # network, rate limit, 5xx, timeout
                last_exc = exc
                if _is_rate_limit(exc) and rate_limits < RATE_LIMIT_RETRIES:
                    rate_limits += 1
                    delay = _retry_delay(exc, 15.0)
                    self._throttle.record(estimated)  # count the rejected request's budget
                    log.warning("Provider rate limit hit (%s): waiting %.0fs, then retrying "
                                "(retry %d of %d).", node, delay, rate_limits, RATE_LIMIT_RETRIES)
                    if run_id:
                        audit.log(run_id, "rate_limited", node,
                                  {"wait_s": delay, "retry": rate_limits})
                    time.sleep(delay)
                    continue
                if "json_validate_failed" in str(exc):  # provider-side JSON check failed
                    log.warning("Model returned malformed JSON (%s); asking again.", node)
                    if run_id:
                        audit.log(run_id, "structured_output_invalid", node,
                                  {"attempt": attempt, "error": "provider json_validate_failed"})
                    if errors >= len(TRANSPORT_BACKOFF):
                        break
                    errors += 1
                    continue
                log.warning("LLM transport error (%s): %s", node, str(exc)[:300])
                if run_id:
                    audit.log(run_id, "llm_error", node,
                              {"error": f"{type(exc).__name__}: {str(exc)[:300]}", "try": tries})
                if errors >= len(TRANSPORT_BACKOFF):
                    break
                time.sleep(_retry_delay(exc, TRANSPORT_BACKOFF[errors]))
                errors += 1
        raise LLMUnavailableError(
            f"LLM provider unavailable after retries: {type(last_exc).__name__}: {last_exc}"
        )

    def __call__(self, schema: type[T], system: str, user: str, *, run_id: str | None,
                 node: str) -> T:
        messages = [SystemMessage(system + schema_instructions(schema, system)), HumanMessage(user)]
        for attempt in (1, 2):
            resp = self._invoke(messages, run_id, node, attempt)
            raw = resp.content if isinstance(resp.content, str) else str(resp.content)
            if node == "reason" and failures.should_fail(run_id, "bad_llm_output"):
                if run_id:
                    audit.log(run_id, "failure_injected", node, {"mode": "bad_llm_output"})
                raw = raw[: len(raw) // 2]  # truncate to make it invalid JSON
            try:
                result = schema.model_validate(extract_json(raw))
                if attempt == 2 and run_id:
                    audit.log(run_id, "recovered", node, {"via": "structured_output_repair"})
                return result
            except (ValueError, ValidationError) as exc:
                err = str(exc)[:1500]
                if run_id:
                    audit.log(run_id, "structured_output_invalid", node,
                              {"attempt": attempt, "error": err})
                if attempt == 2:
                    raise StructuredOutputError(
                        f"The agent could not produce a valid {schema.__name__}: {err}"
                    ) from exc
                messages = messages + [
                    AIMessage(raw),
                    HumanMessage(
                        "Your previous reply was not valid for the required schema. Error:\n"
                        f"{err}\nReturn the corrected JSON object only."
                    ),
                ]
        raise StructuredOutputError("unreachable")
