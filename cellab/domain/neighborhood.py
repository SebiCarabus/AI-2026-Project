"""Neighbourhoods: which cells a cell looks at, and in what order."""
from __future__ import annotations

import itertools
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

Offset = tuple[int, ...]

# Built-in position names for 2D radius-1 presets, as [row, col] offsets (row 0 is the top).
COMPASS: dict[Offset, str] = {
    (-1, 0): "north", (-1, 1): "north_east", (0, 1): "east", (1, 1): "south_east",
    (1, 0): "south", (1, -1): "south_west", (0, -1): "west", (-1, -1): "north_west",
}


class Neighborhood(ABC):
    """An ordered list of offsets relative to a cell, some of which may be named.

    Order matters: lookup-table rules read the neighbour vector positionally, and
    position names resolve to indices into that vector.
    """

    @abstractmethod
    def offsets(self) -> tuple[Offset, ...]:
        ...

    def names(self) -> Mapping[str, int]:
        """Position name -> index into offsets(). Empty if positions are unnamed."""
        return {}

    def index_of(self, name: str) -> int:
        names = self.names()
        if name not in names:
            valid = ", ".join(names) or "none"
            raise KeyError(f"unknown position {name!r} (this neighbourhood has: {valid})")
        return names[name]

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


def _compass_names(n: Neighborhood, radius: int, dimensions: int) -> dict[str, int]:
    """Compass names for 2D presets with radius 1; larger radii have no names."""
    if radius != 1 or dimensions != 2:
        return {}
    return {COMPASS[o]: i for i, o in enumerate(n.offsets()) if o in COMPASS}


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

    def names(self) -> Mapping[str, int]:
        return _compass_names(self, self.radius, self.dimensions)


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

    def names(self) -> Mapping[str, int]:
        return _compass_names(self, self.radius, self.dimensions)


@dataclass(frozen=True)
class Diagonal(Neighborhood):
    """Cells on the diagonals up to `radius` (4 cells in 2D for radius 1). Never the centre."""

    radius: int = 1
    dimensions: int = 2
    _offsets: tuple[Offset, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        r = self.radius
        if r < 1:
            raise ValueError("radius must be >= 1")
        offs = [
            o for o in itertools.product(range(-r, r + 1), repeat=self.dimensions)
            if o[0] != 0 and len({abs(c) for c in o}) == 1
        ]
        object.__setattr__(self, "_offsets", _validated(offs))

    def offsets(self) -> tuple[Offset, ...]:
        return self._offsets

    def names(self) -> Mapping[str, int]:
        return _compass_names(self, self.radius, self.dimensions)


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
    """Any explicit list of offsets, in the order given.

    `labels`, if given, names each point (same order and length as `points`).
    Custom.named({"left": (0, -1), "right": (0, 1)}) is the convenient form.
    """

    points: tuple[Offset, ...]
    labels: tuple[str, ...] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "points", _validated(self.points))
        if self.labels is not None:
            names = tuple(self.labels)
            if len(names) != len(self.points):
                raise ValueError(f"{len(names)} names given for {len(self.points)} points")
            if any(not isinstance(n, str) or not n for n in names):
                raise ValueError("position names must be non-empty strings")
            if len(set(names)) != len(names):
                raise ValueError("position names must be unique")
            object.__setattr__(self, "labels", names)

    @classmethod
    def named(cls, points: Mapping[str, Offset]) -> Custom:
        return cls(tuple(points.values()), tuple(points.keys()))

    def offsets(self) -> tuple[Offset, ...]:
        return self.points

    def names(self) -> Mapping[str, int]:
        return {} if self.labels is None else {n: i for i, n in enumerate(self.labels)}

    def __repr__(self) -> str:
        if self.labels is None:
            return f"Custom(points={self.points})"
        return f"Custom.named({dict(zip(self.labels, self.points))})"