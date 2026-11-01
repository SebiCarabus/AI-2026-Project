"""Structured spec errors with location paths."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpecIssue:
    path: str
    message: str

    def __str__(self) -> str:
        if self.path:
            return f"{self.path}: {self.message}"
        return self.message


class SpecError(ValueError):
    def __init__(self, issues: list[SpecIssue] | SpecIssue | str):
        if isinstance(issues, SpecIssue):
            issues = [issues]
        elif isinstance(issues, str):
            issues = [SpecIssue("", issues)]
        self.issues = list(issues)
        msg = "; ".join(str(i) for i in self.issues)
        super().__init__(msg)


