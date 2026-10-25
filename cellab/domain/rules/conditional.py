"""Conditional rules: ordered (condition -> target) branches, first match wins.

Conditions and targets may be random (WithProbability, RandomChoice); a rule that
contains any of them reports is_deterministic == False and needs an rng to run.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from .base import Rule, check_state

Rng = np.random.Generator | None


def _require_rng(rng: Rng, who: str) -> np.random.Generator:
    if rng is None:
        raise ValueError(f"{who} is random and needs an rng (np.random.default_rng(seed))")
    return rng


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
class CountInRange(Condition):
    """minimum <= (neighbours in `state`) <= maximum. Either bound may be None."""

    state: int
    minimum: int | None = None
    maximum: int | None = None

    @classmethod
    def at_least(cls, state: int, n: int) -> CountInRange:
        return cls(state, minimum=n)

    @classmethod
    def at_most(cls, state: int, n: int) -> CountInRange:
        return cls(state, maximum=n)

    @classmethod
    def exactly(cls, state: int, n: int) -> CountInRange:
        return cls(state, minimum=n, maximum=n)

    def mask(self, current, neighbours, rng=None):
        counts = np.count_nonzero(neighbours == self.state, axis=-1)
        result = np.ones(current.shape, dtype=bool)
        if self.minimum is not None:
            result &= counts >= self.minimum
        if self.maximum is not None:
            result &= counts <= self.maximum
        return result

    def validate(self, num_states, neighborhood_size):
        check_state(self.state, num_states, "CountInRange state")
        if self.minimum is None and self.maximum is None:
            raise ValueError("CountInRange needs at least one bound")
        lo = 0 if self.minimum is None else self.minimum
        hi = neighborhood_size if self.maximum is None else self.maximum
        if lo > hi or lo > neighborhood_size or hi < 0:
            raise ValueError(f"count range [{lo}, {hi}] can never hold with {neighborhood_size} neighbours")


@dataclass(frozen=True)
class WithProbability(Condition):
    """True for each cell independently with probability p.

    Combine with other conditions: CellIs(TREE) & WithProbability(0.001).
    If the draw fails, evaluation simply falls through to the next branch.
    """

    p: float

    def mask(self, current, neighbours, rng=None):
        return _require_rng(rng, "WithProbability").random(current.shape) < self.p

    def validate(self, num_states, neighborhood_size):
        if not 0.0 <= self.p <= 1.0:
            raise ValueError(f"probability {self.p} is not in [0, 1]")

    @property
    def is_deterministic(self) -> bool:
        return self.p in (0.0, 1.0)


@dataclass(frozen=True)
class And(Condition):
    parts: tuple[Condition, ...]

    def mask(self, current, neighbours, rng=None):
        return np.logical_and.reduce([p.mask(current, neighbours, rng) for p in self.parts])

    def validate(self, num_states, neighborhood_size):
        for p in self.parts:
            p.validate(num_states, neighborhood_size)

    @property
    def is_deterministic(self) -> bool:
        return all(p.is_deterministic for p in self.parts)


@dataclass(frozen=True)
class Or(Condition):
    parts: tuple[Condition, ...]

    def mask(self, current, neighbours, rng=None):
        return np.logical_or.reduce([p.mask(current, neighbours, rng) for p in self.parts])

    def validate(self, num_states, neighborhood_size):
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