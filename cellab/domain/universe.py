"""Universe: one generation of a world, independent of how the world is stored.

Simulators and queries talk to this interface. ArrayUniverse holds a bounded world
as one Grid; TreeUniverse stores a sparse, hash-consed binary tree or quadtree.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

import numpy as np

from .automaton import Automaton
from .grid import Grid


class Universe(ABC):
    """An immutable (automaton, world, generation) triple. step() returns a new universe."""

    automaton: Automaton
    generation: int

    @abstractmethod
    def step(self, n: int = 1, rng: np.random.Generator | None = None) -> Universe:
        """The universe `n` generations later. Stochastic automata need `rng`."""

    @abstractmethod
    def counts(self) -> dict[int, int]:
        """State -> number of cells in it. Infinite worlds leave out state 0."""

    @abstractmethod
    def window(self, start: Sequence[int], shape: Sequence[int]) -> np.ndarray:
        """The cells of the box starting at `start` (e.g. [row, col]) with size `shape`."""

    @abstractmethod
    def fingerprint(self) -> str:
        """Equal worlds give equal strings; used to detect stability and cycles."""

    @property
    @abstractmethod
    def bounds(self) -> tuple[int, ...] | None:
        """The world's shape, or None if it is unbounded."""


class ArrayUniverse(Universe):
    """A bounded world held as a single Grid."""

    def __init__(self, automaton: Automaton, grid: Grid, generation: int = 0, *, check: bool = True) -> None:
        if generation < 0:
            raise ValueError("generation must be >= 0")
        if check:
            automaton.check_grid(grid)
        self.automaton = automaton
        self.grid = grid
        self.generation = generation

    def step(self, n: int = 1, rng: np.random.Generator | None = None) -> ArrayUniverse:
        if n < 0:
            raise ValueError("n must be >= 0")
        grid = self.grid
        for _ in range(n):
            grid = self.automaton.step(grid, rng, check=False)  # checked once, in __init__
        return ArrayUniverse(self.automaton, grid, self.generation + n, check=False)

    def counts(self) -> dict[int, int]:
        return {s: int(c) for s, c in enumerate(self.grid.counts(self.automaton.num_states))}

    def window(self, start: Sequence[int], shape: Sequence[int]) -> np.ndarray:
        if len(start) != self.grid.ndim or len(shape) != self.grid.ndim:
            raise ValueError(f"start and shape need {self.grid.ndim} values")
        for s, n, size in zip(start, shape, self.grid.shape):
            if n < 0 or s < 0 or s + n > size:
                raise ValueError(f"window {list(start)} + {list(shape)} is outside the grid {self.grid.shape}")
        return self.grid.cells[tuple(slice(s, s + n) for s, n in zip(start, shape))]

    def fingerprint(self) -> str:
        return self.grid.hash()

    @property
    def bounds(self) -> tuple[int, ...]:
        return self.grid.shape

    def __repr__(self) -> str:
        return f"ArrayUniverse({self.automaton.name!r}, generation={self.generation}, {self.grid!r})"
