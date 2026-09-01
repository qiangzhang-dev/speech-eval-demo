"""Deterministic local providers for tests and offline demonstrations."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from ..diagnosis import DiagnosticContext, DiagnosticResult


class StaticDiagnosticProvider:
    def __init__(self, response: DiagnosticResult | dict[str, Any] | str) -> None:
        self.response = response
        self.calls = 0

    def diagnose(self, context: DiagnosticContext) -> DiagnosticResult | dict[str, Any] | str:
        self.calls += 1
        return self.response


class SequenceDiagnosticProvider:
    """Return or raise configured events in order, then repeat the last one."""

    def __init__(self, events: Iterable[Any]) -> None:
        self.events = list(events)
        if not self.events:
            raise ValueError("events must not be empty")
        self.calls = 0

    def diagnose(self, context: DiagnosticContext) -> Any:
        index = min(self.calls, len(self.events) - 1)
        event = self.events[index]
        self.calls += 1
        if isinstance(event, BaseException):
            raise event
        if isinstance(event, Callable):
            return event(context)
        return event


FakeDiagnosticProvider = SequenceDiagnosticProvider

