"""Typed YAML spec models (the parser's intermediate representation)."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator, model_validator

if TYPE_CHECKING:
    from domain import Automaton, Grid


_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CellTypeSpec(_Model):
    """Display metadata for one cell state."""

    symbol: str
    color: str

    @field_validator("symbol")
    @classmethod
    def _check_symbol(cls, value: str) -> str:
        if len(value) != 1 or value.isspace():
            raise ValueError("symbol must be exactly one non-whitespace character")
        return value

    @field_validator("color")
    @classmethod
    def _check_color(cls, value: str) -> str:
        if not _COLOR_RE.fullmatch(value):
            raise ValueError("color must be a 6-digit hex colour such as '#ffffff'")
        return value


class FixedBoundarySpec(_Model):
    """A fixed boundary; `value` names the cell type outside the grid."""

    type: Literal["fixed"]
    value: StrictStr


BoundarySpec = Literal["torus", "periodic", "reflect"] | FixedBoundarySpec


class WorldSpec(_Model):
    """World size and boundary. Two dimensions use [width, height]."""

    size: list[Annotated[StrictInt, Field(gt=0)]]
    boundary: BoundarySpec

    @field_validator("size")
    @classmethod
    def _check_size(cls, value: list[int]) -> list[int]:
        if len(value) not in (1, 2):
            raise ValueError(
                "world.size must contain one value (1D) or two values [width, height] (2D)"
            )
        return value

    @property
    def ndim(self) -> int:
        return len(self.size)

    @property
    def width(self) -> int:
        return self.size[0]

    @property
    def height(self) -> int:
        return self.size[1] if self.ndim == 2 else 1

    @property
    def grid_shape(self) -> tuple[int, ...]:
        return (self.size[0],) if self.ndim == 1 else (self.size[1], self.size[0])


class MooreSpec(_Model):
    type: Literal["moore"]
    radius: Annotated[StrictInt, Field(ge=1)] = 1
    dimensions: Annotated[StrictInt, Field(ge=1, le=2)] | None = None


class VonNeumannSpec(_Model):
    type: Literal["von_neumann"]
    radius: Annotated[StrictInt, Field(ge=1)] = 1
    dimensions: Annotated[StrictInt, Field(ge=1, le=2)] | None = None


class LineSpec(_Model):
    type: Literal["line"]
    radius: Annotated[StrictInt, Field(ge=1)] = 1


class CustomSpec(_Model):
    type: Literal["custom"]
    points: list[list[StrictInt]]

    @field_validator("points")
    @classmethod
    def _check_points(cls, value: list[list[int]]) -> list[list[int]]:
        if not value:
            raise ValueError("custom neighbourhood needs at least one point")
        lengths = {len(point) for point in value}
        if len(lengths) != 1 or next(iter(lengths)) not in (1, 2):
            raise ValueError("all custom points must have the same 1 or 2 dimensions")
        tuples = [tuple(point) for point in value]
        if len(set(tuples)) != len(tuples):
            raise ValueError("custom neighbourhood points must be unique")
        return value


NeighborhoodSpec = Annotated[
    MooreSpec | VonNeumannSpec | LineSpec | CustomSpec,
    Field(discriminator="type"),
]


class RandomTargetSpec(_Model):
    """Become one of several states with the given probabilities."""

    random: dict[StrictStr, float]

    @field_validator("random")
    @classmethod
    def _check_random(cls, value: dict[str, float]) -> dict[str, float]:
        if not value:
            raise ValueError("random target needs at least one state")
        for probability in value.values():
            if probability < 0.0:
                raise ValueError("random target probabilities must be non-negative")
        return value


TargetSpec = StrictStr | RandomTargetSpec
CountValue = StrictInt | list[StrictInt | None]


class WhenSpec(_Model):
    """One conditional test. All present parts are ANDed."""

    cell: StrictStr | None = None
    count: dict[StrictStr, CountValue] | None = None
    probability: Annotated[float, Field(ge=0.0, le=1.0)] | None = None

    @model_validator(mode="after")
    def _at_least_one_part(self) -> WhenSpec:
        if self.cell is None and not self.count and self.probability is None:
            raise ValueError("when must contain at least one of cell, count or probability")
        return self


class BranchSpec(_Model):
    """Either a conditional branch (`when` + `then`) or the final `otherwise`."""

    when: WhenSpec | None = None
    then: TargetSpec | None = None
    otherwise: TargetSpec | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> BranchSpec:
        conditional = self.when is not None or self.then is not None
        default = self.otherwise is not None
        if conditional and default:
            raise ValueError("a rule branch cannot contain both when/then and otherwise")
        if conditional and (self.when is None or self.then is None):
            raise ValueError("a rule branch needs both when and then")
        if not conditional and not default:
            raise ValueError("a rule branch needs either when+then or otherwise")
        return self


class TotalisticRuleSpec(_Model):
    type: Literal["totalistic"]
    counted: StrictStr
    transitions: dict[StrictStr, dict[StrictInt, StrictStr]]
    default: StrictStr | None = None


class LookupEntrySpec(_Model):
    pattern: list[StrictStr]
    then: StrictStr


class LookupRuleSpec(_Model):
    type: Literal["lookup"]
    table: list[LookupEntrySpec]
    default: StrictStr | None = None


RuleSpec = Annotated[
    TotalisticRuleSpec | LookupRuleSpec,
    Field(discriminator="type"),
]


class InitialStateSpec(_Model):
    """Explicit initial board; unlisted cells use `fill` (or the first cell type)."""

    fill: StrictStr | None = None
    cells: dict[StrictStr, list[list[StrictInt]]] = Field(default_factory=dict)


class AutomatonSpec(_Model):
    """A fully parsed and validated automaton specification."""

    schema_version: Literal[1]
    name: StrictStr
    world: WorldSpec
    neighborhood: NeighborhoodSpec
    cell_types: dict[StrictStr, CellTypeSpec]
    rules: list[BranchSpec] | None = None
    rule: RuleSpec | None = None
    initial_state: InitialStateSpec | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be empty")
        return value

    @field_validator("cell_types")
    @classmethod
    def _check_cell_types(cls, value: dict[str, CellTypeSpec]) -> dict[str, CellTypeSpec]:
        if not 2 <= len(value) <= 256:
            raise ValueError("an automaton needs between 2 and 256 cell types")
        if any(not name for name in value):
            raise ValueError("cell type names must not be empty")
        symbols = [cell.symbol for cell in value.values()]
        if len(set(symbols)) != len(symbols):
            raise ValueError("cell type symbols must be unique")
        return value

    @model_validator(mode="after")
    def _exactly_one_rule_form(self) -> AutomatonSpec:
        if (self.rules is None) == (self.rule is None):
            raise ValueError("exactly one of 'rules' and 'rule' must be present")
        return self

    @property
    def cell_type_names(self) -> tuple[str, ...]:
        return tuple(self.cell_types)

    def compile(self) -> Automaton:
        from .compiler import compile_automaton

        return compile_automaton(self)

    def build_initial_state(self) -> Grid | None:
        from .compiler import build_initial_state

        return build_initial_state(self)
