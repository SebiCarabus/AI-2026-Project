"""Load YAML, apply structural validation, then semantic validation."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .compiler import validate_spec
from .errors import SpecError, SpecIssue
from .models import AutomatonSpec


class _DuplicateKeyError(Exception):
    def __init__(self, key: object, mark) -> None:
        self.key = key
        self.mark = mark
        super().__init__(f"duplicate key {key!r}")


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of silently overwriting."""

    def construct_mapping(self, node, deep: bool = False):
        self.flatten_mapping(node)
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                hash(key)
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found unhashable key ({key!r})",
                    key_node.start_mark,
                ) from exc
            if key in mapping:
                raise _DuplicateKeyError(key, key_node.start_mark)
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


_UNION_TAGS = frozenset(
    {"moore", "von_neumann", "line", "custom", "fixed", "totalistic", "lookup"}
)
_UNION_PARENTS = frozenset({"neighborhood", "boundary", "rule"})
_MODEL_TAGS = frozenset(
    {
        "FixedBoundarySpec",
        "RandomTargetSpec",
        "TotalisticRuleSpec",
        "LookupRuleSpec",
        "MooreSpec",
        "VonNeumannSpec",
        "LineSpec",
        "CustomSpec",
    }
)
_TARGET_PARENTS = frozenset({"then", "otherwise"})
_UNION_ARTIFACT_TYPES = frozenset({"string_type", "model_type", "literal_error"})


def parse_spec(text: str) -> AutomatonSpec:
    """Parse and validate one YAML automaton spec."""
    if not isinstance(text, str):
        raise TypeError("parse_spec expects the YAML document as a string")

    data = _load_yaml(text)
    if data is None:
        raise SpecError([SpecIssue("<spec>", "the YAML document is empty")])
    if not isinstance(data, dict):
        raise SpecError([SpecIssue("<spec>", "the top-level YAML value must be a mapping")])

    try:
        spec = AutomatonSpec.model_validate(data)
    except ValidationError as exc:
        raise SpecError(_pydantic_issues(exc)) from exc

    issues = validate_spec(spec)
    if issues:
        raise SpecError(issues)
    return spec


def load_spec(path: str | Path) -> AutomatonSpec:
    """Read a UTF-8 YAML file and parse it."""
    return parse_spec(Path(path).read_text(encoding="utf-8"))


def _load_yaml(text: str):
    try:
        return yaml.load(text, Loader=_UniqueKeyLoader)
    except _DuplicateKeyError as exc:
        mark = exc.mark
        raise SpecError(
            [
                SpecIssue(
                    "<yaml>",
                    f"duplicate key {exc.key!r} at line {mark.line + 1}, "
                    f"column {mark.column + 1}",
                )
            ]
        ) from exc
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        problem = getattr(exc, "problem", None) or str(exc)
        if mark is not None:
            problem = f"{problem} at line {mark.line + 1}, column {mark.column + 1}"
        raise SpecError([SpecIssue("<yaml>", problem)]) from exc


def _pydantic_issues(exc: ValidationError) -> list[SpecIssue]:
    raw: list[tuple[tuple[object, ...], str, str, str]] = []
    for error in exc.errors():
        parts = tuple(_clean_loc(error.get("loc", ())))
        raw.append(
            (
                parts,
                _format_path(parts),
                _clean_message(error.get("msg", "invalid value")),
                str(error.get("type", "")),
            )
        )

    messages: dict[str, list[str]] = {}
    order: list[str] = []
    for parts, path, message, error_type in raw:
        if error_type in _UNION_ARTIFACT_TYPES and any(
            len(other_parts) > len(parts) and other_parts[: len(parts)] == parts
            for other_parts, _, _, _ in raw
        ):
            continue
        if path not in messages:
            messages[path] = []
            order.append(path)
        if message not in messages[path]:
            messages[path].append(message)

    return [SpecIssue(path, "; ".join(messages[path])) for path in order] or [
        SpecIssue("<spec>", "invalid specification")
    ]


def _clean_loc(loc) -> list[object]:
    cleaned: list[object] = []
    for part in loc:
        if isinstance(part, str) and cleaned:
            parent = cleaned[-1]
            if part in _UNION_TAGS and parent in _UNION_PARENTS:
                continue
            if part in _MODEL_TAGS and parent in _UNION_PARENTS | _TARGET_PARENTS:
                continue
            if part == "str" and parent in _TARGET_PARENTS:
                continue
            if part.startswith("literal[") and parent in _UNION_PARENTS:
                continue
        cleaned.append(part)
    return cleaned


def _format_loc(loc) -> str:
    return _format_path(_clean_loc(loc))


def _format_path(parts) -> str:
    path = ""
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path = f"{path}.{part}" if path else str(part)
    return path or "<spec>"


def _clean_message(message: str) -> str:
    prefix = "Value error, "
    return message[len(prefix) :] if message.startswith(prefix) else message
