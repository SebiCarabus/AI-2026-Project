"""Neighbourhoods: which cells a cell looks at, and in what order."""
from __future__ import annotations

import itertools
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np

Offset = tuple[int, ...]


class Neighborhood(ABC):
    """An ordered list of offsets relative to a cell.

    Order matters: lookup-table rules read the neighbour vector positionally.
    """

    @abstractmethod
    def offsets(self) -> tuple[Offset, ...]:
        ...

    @property
    def ndim(self) -> int:
        return len(self.offsets()[0])

    @property
    def size(self) -> int:
        return len(self.offsets())

    @property
    def reach(self) -> int:
        """Largest distance along any axis; this is how far the boundary must pad."""
        return max(abs(c) for off in self.offsets() for c in off)

    def gather(self, padded: np.ndarray, shape: tuple[int, ...]) -> np.ndarray:
        """Collect neighbour values for every cell.

        `padded` is the grid already padded by `reach` on every side.
        Returns an array of shape (*shape, size): one neighbour vector per cell.
        """
        r = self.reach
        layers = [
            padded[tuple(slice(r + o, r + o + n) for o, n in zip(off, shape))]
            for off in self.offsets()
        ]
        return np.stack(layers, axis=-1)


def _validated(offsets) -> tuple[Offset, ...]:
    offs = tuple(tuple(int(c) for c in o) for o in offsets)
    if not offs:
        raise ValueError("a neighbourhood needs at least one offset")
    if len({len(o) for o in offs}) != 1 or len(offs[0]) == 0:
        raise ValueError("all offsets must have the same, non-zero number of dimensions")
    if len(set(offs)) != len(offs):
        raise ValueError("offsets must be unique")
    return offs


@dataclass(frozen=True)
class Moore(Neighborhood):
    """All cells within Chebyshev distance `radius` (8 cells in 2D for radius 1)."""

    radius: int = 1
    dimensions: int = 2
    include_center: bool = False
    _offsets: tuple[Offset, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        r = self.radius
        if r < 1:
            raise ValueError("radius must be >= 1")
        offs = [
            o for o in itertools.product(range(-r, r + 1), repeat=self.dimensions)
            if self.include_center or any(o)
        ]
        object.__setattr__(self, "_offsets", _validated(offs))

    def offsets(self) -> tuple[Offset, ...]:
        return self._offsets


@dataclass(frozen=True)
class VonNeumann(Neighborhood):
    """All cells within Manhattan distance `radius` (4 cells in 2D for radius 1)."""

    radius: int = 1
    dimensions: int = 2
    include_center: bool = False
    _offsets: tuple[Offset, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        r = self.radius
        if r < 1:
            raise ValueError("radius must be >= 1")
        offs = [
            o for o in itertools.product(range(-r, r + 1), repeat=self.dimensions)
            if sum(map(abs, o)) <= r and (self.include_center or any(o))
        ]
        object.__setattr__(self, "_offsets", _validated(offs))

    def offsets(self) -> tuple[Offset, ...]:
        return self._offsets


@dataclass(frozen=True)
class Line(Neighborhood):
    """1D window, ordered left to right. Line(1) gives (left, self, right)."""

    radius: int = 1
    include_center: bool = True
    _offsets: tuple[Offset, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        r = self.radius
        if r < 1:
            raise ValueError("radius must be >= 1")
        offs = [(i,) for i in range(-r, r + 1) if self.include_center or i != 0]
        object.__setattr__(self, "_offsets", _validated(offs))

    def offsets(self) -> tuple[Offset, ...]:
        return self._offsets


@dataclass(frozen=True)
class Custom(Neighborhood):
    """Any explicit list of offsets, in the order given."""

    points: tuple[Offset, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "points", _validated(self.points))

    def offsets(self) -> tuple[Offset, ...]:
        return self.points