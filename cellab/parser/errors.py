"""Structured errors raised by the YAML spec parser."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SpecIssue:
    """One readable problem, located by a dotted/bracketed path."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


class SpecError(ValueError):
    """Raised when a spec cannot be parsed, validated or compiled."""

    def __init__(self, issues: Sequence[SpecIssue] | str) -> None:
        if isinstance(issues, str):
            issues = [SpecIssue("<spec>", issues)]
        self.issues: list[SpecIssue] = list(issues)
        lines = ["invalid automaton spec:"]
        lines.extend(f"  - {issue}" for issue in self.issues)
        super().__init__("\n".join(lines))
