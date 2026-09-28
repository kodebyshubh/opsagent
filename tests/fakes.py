"""FakeLLM: scripted structured outputs so orchestration is testable offline (TASK-012)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from opsagent.models import StructuredOutputError


class FakeLLM:
    """Returns scripted responses per schema name ('Plan', 'Decision'), in order.

    A scripted item may be a dict, a BaseModel, or a callable(system, user) -> dict.
    The string 'INVALID' makes the call raise StructuredOutputError.
    """

    def __init__(self, script: dict[str, list[Any]]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[dict] = []

    def __call__(self, schema: type[BaseModel], system: str, user: str, *, run_id, node):
        self.calls.append({"schema": schema.__name__, "node": node, "system": system,
                           "user": user})
        queue = self.script.get(schema.__name__)
        if not queue:
            raise AssertionError(f"FakeLLM has no scripted response left for {schema.__name__}")
        item = queue.pop(0)
        if item == "INVALID":
            raise StructuredOutputError("scripted invalid output")
        if callable(item):
            item = item(system, user)
        return schema.model_validate(item if not isinstance(item, BaseModel) else item.model_dump())


class StubChatModel:
    """Minimal stand-in for a LangChain chat model: returns scripted raw strings."""

    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = list(outputs)
        self.calls = 0

    def invoke(self, messages):
        from langchain_core.messages import AIMessage

        self.calls += 1
        item = self.outputs.pop(0)
        if isinstance(item, Exception):
            raise item
        return AIMessage(content=item)
