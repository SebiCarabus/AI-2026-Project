"""Conditional rules: ordered (condition -> target) branches, first match wins.

Conditions and targets may be random (WithProbability, RandomChoice); a rule that
contains any of them reports is_deterministic == False and needs an rng to run.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from .base import Rule, check_state

Rng = np.random.Generator | None
States = frozenset[int]

_LUT_SIZE = 256  # one entry per possible uint8 cell value


def _require_rng(rng: Rng, who: str) -> np.random.Generator:
    if rng is None:
        raise ValueError(f"{who} is random and needs an rng (np.random.default_rng(seed))")
    return rng


def _as_states(value: int | Iterable[int]) -> States:
    """One state or several -> frozenset of ints."""
    if isinstance(value, (int, np.integer)):
        return frozenset((int(value),))
    return frozenset(int(s) for s in value)


def _lut(states: States) -> np.ndarray:
    """Boolean table indexed by cell value: lut[cells] is True where a cell is in `states`.

    Out-of-range states are left out here and reported by validate().
    """
    lut = np.zeros(_LUT_SIZE, dtype=bool)
    lut[[s for s in states if 0 <= s < _LUT_SIZE]] = True
    return lut


def _check_states(states: States, num_states: int, what: str) -> None:
    if not states:
        raise ValueError(f"{what} needs at least one state")
    for s in sorted(states):
        check_state(s, num_states, what)


def _count(neighbours: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Per cell, how many neighbour offsets hold a state in the lut."""
    return np.count_nonzero(lut[neighbours], axis=-1)


# --- Conditions -------------------------------------------------------------
class Condition(ABC):
    """A per-cell boolean test. Combine with &, | and ~."""

    @abstractmethod
    def mask(self, current: np.ndarray, neighbours: np.ndarray, rng: Rng = None) -> np.ndarray:
        """Boolean array of shape S: True where the condition holds."""

    def validate(self, num_states: int, neighborhood_size: int) -> None:
        pass

    @property
    def is_deterministic(self) -> bool:
        return True

    def __and__(self, other: Condition) -> Condition:
        return And((self, other))

    def __or__(self, other: Condition) -> Condition:
        return Or((self, other))

    def __invert__(self) -> Condition:
        return Not(self)


@dataclass(frozen=True)
class CellIs(Condition):
    """The cell itself is in `state`."""

    state: int

    def mask(self, current, neighbours, rng=None):
        return current == self.state

    def validate(self, num_states, neighborhood_size):
        check_state(self.state, num_states, "CellIs state")


@dataclass(frozen=True)
class CellIn(Condition):
    """The cell itself is in any of `states` (one state or a group)."""

    states: States
    _lut: np.ndarray = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "states", _as_states(self.states))
        object.__setattr__(self, "_lut", _lut(self.states))

    def mask(self, current, neighbours, rng=None):
        return self._lut[current]

    def validate(self, num_states, neighborhood_size):
        _check_states(self.states, num_states, "CellIn state")


@dataclass(frozen=True)
class CountInRange(Condition):
    """minimum <= (neighbours in any of `states`) <= maximum. Either bound may be None.

    `states` is one state or a group. Counting is per offset: on a tiny wrapped grid,
    two offsets landing on the same cell count twice. A range that can never hold
    with this neighbourhood (e.g. at least 9 of 8) is allowed and simply never matches.
    """

    states: States
    minimum: int | None = None
    maximum: int | None = None
    _lut: np.ndarray = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "states", _as_states(self.states))
        object.__setattr__(self, "_lut", _lut(self.states))

    @classmethod
    def at_least(cls, states: int | Iterable[int], n: int) -> CountInRange:
        return cls(states, minimum=n)

    @classmethod
    def at_most(cls, states: int | Iterable[int], n: int) -> CountInRange:
        return cls(states, maximum=n)

    @classmethod
    def exactly(cls, states: int | Iterable[int], n: int) -> CountInRange:
        return cls(states, minimum=n, maximum=n)

    def mask(self, current, neighbours, rng=None):
        counts = _count(neighbours, self._lut)
        result = np.ones(current.shape, dtype=bool)
        if self.minimum is not None:
            result &= counts >= self.minimum
        if self.maximum is not None:
            result &= counts <= self.maximum
        return result

    def validate(self, num_states, neighborhood_size):
        _check_states(self.states, num_states, "CountInRange state")
        if self.minimum is None and self.maximum is None:
            raise ValueError("CountInRange needs at least one bound")
        if any(b is not None and b < 0 for b in (self.minimum, self.maximum)):
            raise ValueError("count bounds must be non-negative")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError(f"count range [{self.minimum}, {self.maximum}] has minimum greater than maximum")


@dataclass(frozen=True)
class CountInSet(Condition):
    """(neighbours in any of `states`) is one of `allowed`, e.g. alive: [2, 3]."""

    states: States
    allowed: frozenset[int]
    _lut: np.ndarray = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "states", _as_states(self.states))
        object.__setattr__(self, "allowed", _as_states(self.allowed))
        object.__setattr__(self, "_lut", _lut(self.states))

    def mask(self, current, neighbours, rng=None):
        return np.isin(_count(neighbours, self._lut), list(self.allowed))

    def validate(self, num_states, neighborhood_size):
        _check_states(self.states, num_states, "CountInSet state")
        if not self.allowed:
            raise ValueError("CountInSet needs at least one allowed count")
        if min(self.allowed) < 0:
            raise ValueError("allowed counts must be non-negative")


@dataclass(frozen=True)
class MoreThan(Condition):
    """Strictly more neighbours in `states` than in `than`. A tie is False."""

    states: States
    than: States
    _lut: np.ndarray = field(init=False, repr=False, compare=False)
    _than_lut: np.ndarray = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "states", _as_states(self.states))
        object.__setattr__(self, "than", _as_states(self.than))
        object.__setattr__(self, "_lut", _lut(self.states))
        object.__setattr__(self, "_than_lut", _lut(self.than))

    def mask(self, current, neighbours, rng=None):
        return _count(neighbours, self._lut) > _count(neighbours, self._than_lut)

    def validate(self, num_states, neighborhood_size):
        _check_states(self.states, num_states, "MoreThan state")
        _check_states(self.than, num_states, "MoreThan comparison state")


@dataclass(frozen=True)
class NeighbourIn(Condition):
    """The neighbour at index `position` of the neighbourhood holds any of `states`.

    Resolve a position name to its index with Neighborhood.index_of(name).
    """

    position: int
    states: States
    _lut: np.ndarray = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "states", _as_states(self.states))
        object.__setattr__(self, "_lut", _lut(self.states))

    def mask(self, current, neighbours, rng=None):
        return self._lut[neighbours[..., self.position]]

    def validate(self, num_states, neighborhood_size):
        if not 0 <= self.position < neighborhood_size:
            raise ValueError(f"position {self.position} is outside a neighbourhood of {neighborhood_size} cells")
        _check_states(self.states, num_states, "NeighbourIn state")


@dataclass(frozen=True)
class WithProbability(Condition):
    """True for each cell independently with probability p.

    Combine with other conditions: CellIs(TREE) & WithProbability(0.001).
    If the draw fails, evaluation simply falls through to the next branch.
    """

    p: float

    def mask(self, current, neighbours, rng=None):
        if self.p == 0.0:
            return np.zeros(current.shape, dtype=bool)
        if self.p == 1.0:
            return np.ones(current.shape, dtype=bool)
        return _require_rng(rng, "WithProbability").random(current.shape) < self.p

    def validate(self, num_states, neighborhood_size):
        if not 0.0 <= self.p <= 1.0:
            raise ValueError(f"probability {self.p} is not in [0, 1]")

    @property
    def is_deterministic(self) -> bool:
        return self.p in (0.0, 1.0)


@dataclass(frozen=True)
class And(Condition):
    """All parts hold. Stops evaluating once no cell is left."""

    parts: tuple[Condition, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "parts", tuple(self.parts))

    def mask(self, current, neighbours, rng=None):
        result = self.parts[0].mask(current, neighbours, rng)
        for p in self.parts[1:]:
            if not result.any():
                break
            result = result & p.mask(current, neighbours, rng)
        return result

    def validate(self, num_states, neighborhood_size):
        if not self.parts:
            raise ValueError("And needs at least one part")
        for p in self.parts:
            p.validate(num_states, neighborhood_size)

    @property
    def is_deterministic(self) -> bool:
        return all(p.is_deterministic for p in self.parts)


@dataclass(frozen=True)
class Or(Condition):
    """At least one part holds. Stops evaluating once every cell is covered."""

    parts: tuple[Condition, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "parts", tuple(self.parts))

    def mask(self, current, neighbours, rng=None):
        result = self.parts[0].mask(current, neighbours, rng)
        for p in self.parts[1:]:
            if result.all():
                break
            result = result | p.mask(current, neighbours, rng)
        return result

    def validate(self, num_states, neighborhood_size):
        if not self.parts:
            raise ValueError("Or needs at least one part")
        for p in self.parts:
            p.validate(num_states, neighborhood_size)

    @property
    def is_deterministic(self) -> bool:
        return all(p.is_deterministic for p in self.parts)


@dataclass(frozen=True)
class Not(Condition):
    part: Condition

    def mask(self, current, neighbours, rng=None):
        return ~self.part.mask(current, neighbours, rng)

    def validate(self, num_states, neighborhood_size):
        self.part.validate(num_states, neighborhood_size)

    @property
    def is_deterministic(self) -> bool:
        return self.part.is_deterministic


# --- Targets ----------------------------------------------------------------
class Target(ABC):
    """What a cell becomes when its branch matches."""

    @abstractmethod
    def resolve(self, current: np.ndarray, neighbours: np.ndarray, rng: Rng = None) -> np.ndarray:
        """Array of shape S with the candidate next state for every cell."""

    def validate(self, num_states: int) -> None:
        pass

    @property
    def is_deterministic(self) -> bool:
        return True


@dataclass(frozen=True)
class SetTo(Target):
    """Become `state`."""

    state: int

    def resolve(self, current, neighbours, rng=None):
        return np.full_like(current, self.state)

    def validate(self, num_states):
        check_state(self.state, num_states, "target state")


@dataclass(frozen=True)
class Identity(Target):
    """Stay as you are."""

    def resolve(self, current, neighbours, rng=None):
        return current


@dataclass(frozen=True)
class RandomChoice(Target):
    """Become one of several states, each with its own probability (must sum to 1).

    RandomChoice({EMPTY: 0.7, TREE: 0.3})
    """

    probabilities: Mapping[int, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "probabilities", {int(s): float(p) for s, p in self.probabilities.items()})

    def resolve(self, current, neighbours, rng=None):
        positive = [s for s, p in self.probabilities.items() if p > 0]
        if len(positive) == 1:
            return np.full_like(current, positive[0])
        states = np.fromiter(self.probabilities.keys(), dtype=current.dtype)
        probs = np.fromiter(self.probabilities.values(), dtype=float)
        return _require_rng(rng, "RandomChoice").choice(states, size=current.shape, p=probs)

    def validate(self, num_states):
        if not self.probabilities:
            raise ValueError("RandomChoice needs at least one state")
        for s, p in self.probabilities.items():
            check_state(s, num_states, "RandomChoice state")
            if p < 0:
                raise ValueError(f"probability {p} for state {s} is negative")
        total = sum(self.probabilities.values())
        if not np.isclose(total, 1.0):
            raise ValueError(f"RandomChoice probabilities sum to {total}, not 1")

    @property
    def is_deterministic(self) -> bool:
        return sum(p > 0 for p in self.probabilities.values()) <= 1


# --- The rule ---------------------------------------------------------------
@dataclass(frozen=True)
class Branch:
    condition: Condition
    target: Target


@dataclass(frozen=True)
class ConditionalRule(Rule):
    """Branches are tried top to bottom; the first that matches decides.

    Cells matching no branch use `otherwise` (required, so the rule is always total).
    """

    branches: Sequence[Branch]
    otherwise: Target

    def __post_init__(self) -> None:
        object.__setattr__(self, "branches", tuple(self.branches))

    @property
    def is_deterministic(self) -> bool:
        return self.otherwise.is_deterministic and all(
            b.condition.is_deterministic and b.target.is_deterministic for b in self.branches
        )

    def validate(self, num_states: int, neighborhood_size: int) -> None:
        for i, b in enumerate(self.branches):
            try:
                b.condition.validate(num_states, neighborhood_size)
                b.target.validate(num_states)
            except ValueError as e:
                raise ValueError(f"branch {i}: {e}") from e
        self.otherwise.validate(num_states)

    def apply(self, current, neighbours, num_states, rng=None) -> np.ndarray:
        out = np.empty_like(current)
        decided = np.zeros(current.shape, dtype=bool)
        for b in self.branches:
            hit = b.condition.mask(current, neighbours, rng) & ~decided
            if hit.any():
                out[hit] = b.target.resolve(current, neighbours, rng)[hit]
                decided |= hit
        rest = ~decided
        if rest.any():
            out[rest] = self.otherwise.resolve(current, neighbours, rng)[rest]
        return out