"""Semantic validation and compilation from the typed spec to domain objects."""
from __future__ import annotations

import math
from typing import TypeAlias

import numpy as np

from domain import (
    Automaton,
    Boundary,
    CellType,
    Custom,
    Fixed,
    Grid,
    Line,
    Moore,
    Neighborhood,
    Periodic,
    Reflect,
    VonNeumann,
)
from domain.rules import (
    And,
    Branch,
    CellIs,
    ConditionalRule,
    CountInRange,
    Identity,
    LookupTableRule,
    RandomChoice,
    Rule,
    SetTo,
    Target,
    TotalisticRule,
    WithProbability,
)
from domain.rules.conditional import Condition

from .errors import SpecError, SpecIssue
from .models import (
    AutomatonSpec,
    BranchSpec,
    CustomSpec,
    FixedBoundarySpec,
    LineSpec,
    LookupRuleSpec,
    MooreSpec,
    RandomTargetSpec,
    RuleSpec,
    TotalisticRuleSpec,
    VonNeumannSpec,
    WhenSpec,
)

Index: TypeAlias = dict[str, int]


def validate_spec(spec: AutomatonSpec) -> list[SpecIssue]:
    """Return every semantic issue that structural Pydantic validation cannot see."""
    issues: list[SpecIssue] = []
    index = {name: i for i, name in enumerate(spec.cell_types)}

    neighborhood_size: int | None = None
    try:
        neighborhood_size = _build_neighborhood(spec).size
    except ValueError as exc:
        issues.append(SpecIssue("neighborhood", str(exc)))

    _validate_boundary(spec, index, issues)
    _validate_initial_state(spec, index, issues)

    if spec.rules is not None:
        _validate_conditional_rules(spec.rules, index, neighborhood_size, issues)
    elif spec.rule is not None:
        if isinstance(spec.rule, TotalisticRuleSpec):
            _validate_totalistic(spec.rule, index, neighborhood_size, issues)
        elif isinstance(spec.rule, LookupRuleSpec):
            _validate_lookup(spec.rule, index, neighborhood_size, issues)
        else:
            issues.append(SpecIssue("rule", f"unknown rule family {type(spec.rule).__name__}"))

    return issues


def compile_automaton(spec: AutomatonSpec) -> Automaton:
    """Validate the spec and build the corresponding domain Automaton."""
    issues = validate_spec(spec)
    if issues:
        raise SpecError(issues)

    index = {name: i for i, name in enumerate(spec.cell_types)}
    cell_types = tuple(CellType(name) for name in spec.cell_types)
    neighborhood = _build_neighborhood(spec)
    boundary = _build_boundary(spec, index)

    if spec.rules is not None:
        rule: Rule = _build_conditional_rule(spec.rules, index)
    elif spec.rule is not None:
        rule = _build_family_rule(spec.rule, index)
    else:
        raise SpecError([SpecIssue("<spec>", "exactly one of 'rules' and 'rule' must be present")])

    try:
        return Automaton(spec.name, cell_types, neighborhood, boundary, rule)
    except ValueError as exc:
        raise SpecError([SpecIssue("rule", str(exc))]) from exc


def build_initial_state(spec: AutomatonSpec) -> Grid | None:
    """Build the optional initial Grid described by the spec."""
    issues = validate_spec(spec)
    if issues:
        raise SpecError(issues)
    if spec.initial_state is None:
        return None

    index = {name: i for i, name in enumerate(spec.cell_types)}
    fill_name = spec.initial_state.fill or spec.cell_type_names[0]
    cells = np.full(spec.world.grid_shape, index[fill_name], dtype=np.int64)
    for name, coordinates in spec.initial_state.cells.items():
        state = index[name]
        for coordinate in coordinates:
            cells[tuple(coordinate)] = state
    return Grid(cells)


def _build_neighborhood(spec: AutomatonSpec) -> Neighborhood:
    world_ndim = spec.world.ndim
    neighborhood = spec.neighborhood

    if isinstance(neighborhood, MooreSpec):
        dimensions = neighborhood.dimensions
        if dimensions is None:
            dimensions = world_ndim
        if dimensions != world_ndim:
            raise ValueError(
                f"neighbourhood is {dimensions}D but the world is {world_ndim}D"
            )
        return Moore(radius=neighborhood.radius, dimensions=dimensions)

    if isinstance(neighborhood, VonNeumannSpec):
        dimensions = neighborhood.dimensions
        if dimensions is None:
            dimensions = world_ndim
        if dimensions != world_ndim:
            raise ValueError(
                f"neighbourhood is {dimensions}D but the world is {world_ndim}D"
            )
        return VonNeumann(radius=neighborhood.radius, dimensions=dimensions)

    if isinstance(neighborhood, LineSpec):
        if world_ndim != 1:
            raise ValueError("line neighbourhood requires a 1D world")
        return Line(radius=neighborhood.radius)

    if isinstance(neighborhood, CustomSpec):
        point_dims = len(neighborhood.points[0])
        if point_dims != world_ndim:
            raise ValueError(
                f"custom points are {point_dims}D but the world is {world_ndim}D"
            )
        return Custom(points=tuple(tuple(point) for point in neighborhood.points))

    raise ValueError(f"unknown neighbourhood {type(neighborhood).__name__}")


def _build_boundary(spec: AutomatonSpec, index: Index) -> Boundary:
    boundary = spec.world.boundary
    if isinstance(boundary, str):
        if boundary in ("torus", "periodic"):
            return Periodic()
        if boundary == "reflect":
            return Reflect()
        raise ValueError(f"unknown boundary {boundary!r}")
    if isinstance(boundary, FixedBoundarySpec):
        return Fixed(index[boundary.value])
    raise ValueError(f"unknown boundary {type(boundary).__name__}")


def _build_conditional_rule(branches: list[BranchSpec], index: Index) -> ConditionalRule:
    compiled: list[Branch] = []
    otherwise: Target | None = None
    for branch in branches:
        if branch.otherwise is not None:
            otherwise = _build_target(branch.otherwise, index)
            continue
        if branch.when is None or branch.then is None:
            raise SpecError([SpecIssue("rules", "a rule branch needs both when and then")])
        compiled.append(
            Branch(_build_condition(branch.when, index), _build_target(branch.then, index))
        )
    if otherwise is None:
        raise SpecError([SpecIssue("rules", "exactly one otherwise branch is required")])
    return ConditionalRule(compiled, otherwise=otherwise)


def _build_condition(when: WhenSpec, index: Index) -> Condition:
    parts: list[Condition] = []
    if when.cell is not None:
        parts.append(CellIs(index[when.cell]))
    if when.count:
        for name, value in when.count.items():
            bounds = _count_bounds(value)
            if bounds is None:
                raise SpecError([SpecIssue("when.count", "invalid count range")])
            minimum, maximum = bounds
            parts.append(CountInRange(index[name], minimum=minimum, maximum=maximum))
    if when.probability is not None:
        parts.append(WithProbability(when.probability))
    if len(parts) == 1:
        return parts[0]
    return And(tuple(parts))


def _build_target(target: str | RandomTargetSpec, index: Index) -> Target:
    if isinstance(target, str):
        if target == "same":
            return Identity()
        return SetTo(index[target])
    return RandomChoice(
        {index[name]: probability for name, probability in target.random.items()}
    )


def _build_family_rule(rule: RuleSpec, index: Index) -> Rule:
    if isinstance(rule, TotalisticRuleSpec):
        transitions = {
            index[current]: {count: index[next_state] for count, next_state in mapping.items()}
            for current, mapping in rule.transitions.items()
        }
        default = index[rule.default] if rule.default is not None else None
        return TotalisticRule(
            counted=index[rule.counted],
            transitions=transitions,
            default=default,
        )

    if isinstance(rule, LookupRuleSpec):
        table = {
            tuple(index[name] for name in entry.pattern): index[entry.then]
            for entry in rule.table
        }
        default = index[rule.default] if rule.default is not None else None
        return LookupTableRule(table=table, default=default)

    raise SpecError([SpecIssue("rule", f"unknown rule family {type(rule).__name__}")])


def _validate_boundary(spec: AutomatonSpec, index: Index, issues: list[SpecIssue]) -> None:
    boundary = spec.world.boundary
    if isinstance(boundary, FixedBoundarySpec) and boundary.value not in index:
        issues.append(
            SpecIssue("world.boundary.value", f"unknown cell type {boundary.value!r}")
        )


def _validate_initial_state(
    spec: AutomatonSpec, index: Index, issues: list[SpecIssue]
) -> None:
    initial = spec.initial_state
    if initial is None:
        return

    if initial.fill is not None and initial.fill not in index:
        issues.append(SpecIssue("initial_state.fill", f"unknown cell type {initial.fill!r}"))

    shape = spec.world.grid_shape
    seen: dict[tuple[int, ...], tuple[str, int]] = {}
    for name, coordinates in initial.cells.items():
        if name not in index:
            issues.append(
                SpecIssue(f"initial_state.cells.{name}", f"unknown cell type {name!r}")
            )
            continue
        for position, coordinate in enumerate(coordinates):
            path = f"initial_state.cells.{name}[{position}]"
            if len(coordinate) != spec.world.ndim:
                issues.append(
                    SpecIssue(
                        path,
                        f"coordinate {list(coordinate)} must have "
                        f"{spec.world.ndim} value(s)",
                    )
                )
                continue
            if any(value < 0 or value >= size for value, size in zip(coordinate, shape)):
                if spec.world.ndim == 2:
                    detail = f"height={shape[0]}, width={shape[1]}"
                else:
                    detail = f"length={shape[0]}"
                issues.append(
                    SpecIssue(
                        path,
                        f"coordinate {list(coordinate)} is outside the grid ({detail})",
                    )
                )
                continue

            key = tuple(coordinate)
            previous = seen.get(key)
            if previous is None:
                seen[key] = (name, position)
            elif previous[0] == name:
                issues.append(
                    SpecIssue(path, f"coordinate {list(coordinate)} is listed more than once")
                )
            else:
                previous_name, previous_position = previous
                issues.append(
                    SpecIssue(
                        path,
                        f"coordinate {list(coordinate)} is already assigned to "
                        f"{previous_name!r} at initial_state.cells."
                        f"{previous_name}[{previous_position}]",
                    )
                )


def _validate_conditional_rules(
    rules: list[BranchSpec],
    index: Index,
    neighborhood_size: int | None,
    issues: list[SpecIssue],
) -> None:
    otherwise_indices = [i for i, branch in enumerate(rules) if branch.otherwise is not None]
    if len(otherwise_indices) != 1:
        issues.append(SpecIssue("rules", "exactly one otherwise branch is required"))
    elif otherwise_indices[0] != len(rules) - 1:
        issues.append(
            SpecIssue(
                f"rules[{otherwise_indices[0]}].otherwise",
                "the otherwise branch must be last",
            )
        )

    for i, branch in enumerate(rules):
        if branch.otherwise is not None:
            _validate_target(branch.otherwise, f"rules[{i}].otherwise", index, issues)
            continue
        if branch.when is not None:
            _validate_when(
                branch.when, f"rules[{i}].when", index, neighborhood_size, issues
            )
        if branch.then is not None:
            _validate_target(branch.then, f"rules[{i}].then", index, issues)


def _validate_when(
    when: WhenSpec,
    path: str,
    index: Index,
    neighborhood_size: int | None,
    issues: list[SpecIssue],
) -> None:
    if when.cell is not None and when.cell not in index:
        issues.append(SpecIssue(f"{path}.cell", f"unknown cell type {when.cell!r}"))

    if not when.count:
        return
    for name, value in when.count.items():
        count_path = f"{path}.count.{name}"
        if name not in index:
            issues.append(SpecIssue(count_path, f"unknown cell type {name!r}"))

        bounds = _count_bounds(value)
        if bounds is None:
            issues.append(
                SpecIssue(
                    count_path,
                    "count must be an integer or a two-element [min, max] list",
                )
            )
            continue
        minimum, maximum = bounds

        if minimum is None and maximum is None:
            issues.append(SpecIssue(count_path, "count range needs at least one bound"))
            continue
        if (minimum is not None and minimum < 0) or (
            maximum is not None and maximum < 0
        ):
            issues.append(SpecIssue(count_path, "count bounds must be non-negative"))
        if minimum is not None and maximum is not None and minimum > maximum:
            issues.append(
                SpecIssue(
                    count_path,
                    f"count range [{minimum}, {maximum}] has minimum greater than maximum",
                )
            )
        if (
            neighborhood_size is not None
            and minimum is not None
            and minimum > neighborhood_size
        ):
            issues.append(
                SpecIssue(
                    count_path,
                    f"count range starting at {minimum} can never hold with "
                    f"{neighborhood_size} neighbours",
                )
            )


def _validate_target(
    target: str | RandomTargetSpec, path: str, index: Index, issues: list[SpecIssue]
) -> None:
    if isinstance(target, str):
        if target != "same" and target not in index:
            issues.append(SpecIssue(path, f"unknown cell type {target!r}"))
        return

    for name, probability in target.random.items():
        if name not in index:
            issues.append(
                SpecIssue(f"{path}.random.{name}", f"unknown cell type {name!r}")
            )
        if probability > 1.0:
            issues.append(
                SpecIssue(f"{path}.random.{name}", "probability must be at most 1")
            )

    total = sum(target.random.values())
    if not math.isclose(total, 1.0, rel_tol=1e-5, abs_tol=1e-8):
        issues.append(
            SpecIssue(f"{path}.random", f"probabilities sum to {total:g}, not 1")
        )


def _validate_totalistic(
    rule: TotalisticRuleSpec,
    index: Index,
    neighborhood_size: int | None,
    issues: list[SpecIssue],
) -> None:
    if rule.counted not in index:
        issues.append(SpecIssue("rule.counted", f"unknown cell type {rule.counted!r}"))

    for current, transitions in rule.transitions.items():
        current_path = f"rule.transitions.{current}"
        if current not in index:
            issues.append(SpecIssue(current_path, f"unknown cell type {current!r}"))
        for count, next_state in transitions.items():
            count_path = f"{current_path}.{count}"
            if count < 0:
                issues.append(SpecIssue(count_path, "count must be non-negative"))
            if neighborhood_size is not None and count > neighborhood_size:
                issues.append(
                    SpecIssue(
                        count_path,
                        f"count {count} is impossible with {neighborhood_size} neighbours",
                    )
                )
            if next_state not in index:
                issues.append(
                    SpecIssue(count_path, f"unknown cell type {next_state!r}")
                )

    if rule.default is not None and rule.default not in index:
        issues.append(SpecIssue("rule.default", f"unknown cell type {rule.default!r}"))


def _validate_lookup(
    rule: LookupRuleSpec,
    index: Index,
    neighborhood_size: int | None,
    issues: list[SpecIssue],
) -> None:
    patterns: set[tuple[str, ...]] = set()
    for i, entry in enumerate(rule.table):
        entry_path = f"rule.table[{i}]"
        if neighborhood_size is not None and len(entry.pattern) != neighborhood_size:
            issues.append(
                SpecIssue(
                    f"{entry_path}.pattern",
                    f"pattern has {len(entry.pattern)} values, but the neighbourhood "
                    f"has {neighborhood_size} cells",
                )
            )
        for position, name in enumerate(entry.pattern):
            if name not in index:
                issues.append(
                    SpecIssue(
                        f"{entry_path}.pattern[{position}]",
                        f"unknown cell type {name!r}",
                    )
                )
        if entry.then not in index:
            issues.append(
                SpecIssue(f"{entry_path}.then", f"unknown cell type {entry.then!r}")
            )

        pattern = tuple(entry.pattern)
        if pattern in patterns:
            issues.append(
                SpecIssue(f"{entry_path}.pattern", f"duplicate pattern {list(pattern)}")
            )
        else:
            patterns.add(pattern)

    if rule.default is not None:
        if rule.default not in index:
            issues.append(SpecIssue("rule.default", f"unknown cell type {rule.default!r}"))
    elif neighborhood_size is not None:
        expected = len(index) ** neighborhood_size
        if len(patterns) < expected:
            issues.append(
                SpecIssue(
                    "rule.default",
                    "lookup table is incomplete and no default was given",
                )
            )


def _count_bounds(value: int | list[int | None]) -> tuple[int | None, int | None] | None:
    if isinstance(value, int):
        return value, value
    if len(value) != 2:
        return None
    return value[0], value[1]
