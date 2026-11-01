"""Strict YAML parsing for spec v1."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .errors import SpecError, SpecIssue


IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class _StrictLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _StrictLoader, node):
    if isinstance(node, yaml.MappingNode):
        keys = set()
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=False)
            if isinstance(key, bool) or key is None:
                raise yaml.constructor.ConstructorError(
                    None, None,
                    "name that would read as boolean/null/number must be quoted",
                    key_node.start_mark,
                )
            if isinstance(key, (int, float)) and not isinstance(key, bool):
                raise yaml.constructor.ConstructorError(
                    None, None,
                    "name that would read as boolean/null/number must be quoted",
                    key_node.start_mark,
                )
            if key in keys:
                raise yaml.constructor.ConstructorError(
                    None, None,
                    f"duplicate key {key!r}",
                    key_node.start_mark,
                )
            keys.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


# Also catch when bool/null are used as scalar values that appear in name positions?
# Better to do post-checking
def _check_forbidden_syntax(text: str) -> list[SpecIssue]:
    issues = []
    if re.search(r'&[A-Za-z0-9_-]', text):
        issues.append(SpecIssue("<yaml>", "anchors/aliases/tags/merge keys are not allowed"))
    if re.search(r'(?<!\*)\*(?!\*)[A-Za-z0-9_-]', text):
        issues.append(SpecIssue("<yaml>", "anchors/aliases/tags/merge keys are not allowed"))
    if '!!' in text:
        issues.append(SpecIssue("<yaml>", "anchors/aliases/tags/merge keys are not allowed"))
    if re.search(r'^\s*<<\s*:', text, re.M):
        issues.append(SpecIssue("<yaml>", "anchors/aliases/tags/merge keys are not allowed"))
    return issues


def parse_spec(text: str) -> dict[str, Any]:
    issues = []
    issues.extend(_check_forbidden_syntax(text))
    try:
        data = yaml.load(text, Loader=_StrictLoader)
    except yaml.YAMLError as e:
        msg = str(e)
        mark = getattr(e, 'problem_mark', None)
        if mark is not None:
            issues.append(SpecIssue(f"<yaml>:{mark.line + 1}:{mark.column + 1}", msg))
        else:
            issues.append(SpecIssue("<yaml>", msg))
        raise SpecError(issues)
    if issues:
        raise SpecError(issues)
    if not isinstance(data, dict):
        raise SpecError(SpecIssue("<root>", "top level must be a mapping"))
    return data


def load_spec(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    text = p.read_text(encoding='utf-8')
    return parse_spec(text)
