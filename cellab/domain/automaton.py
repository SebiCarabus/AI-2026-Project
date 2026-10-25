"""Automaton: cell types + neighbourhood + boundary + one rule."""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from .boundary import Boundary
from .grid import MAX_STATES, CellType, Grid
from .neighborhood import Neighborhood
from .rules.base import Rule


class Automaton:
    """A fully assembled cellular automaton. Stateless: step() is a pure function."""

    def __init__(
        self,
        name: str,
        cell_types: Sequence[CellType],
        neighborhood: Neighborhood,
        boundary: Boundary,
        rule: Rule,
    ) -> None:
        cell_types = tuple(cell_types)
        if not 2 <= len(cell_types) <= MAX_STATES:
            raise ValueError(f"an automaton needs between 2 and {MAX_STATES} cell types")
        names = [c.name for c in cell_types]
        if len(set(names)) != len(names):
            raise ValueError(f"cell type names must be unique: {names}")
        rule.validate(len(cell_types), neighborhood.size)

        self.name = name
        self.cell_types = cell_types
        self.neighborhood = neighborhood
        self.boundary = boundary
        self.rule = rule

    @property
    def num_states(self) -> int:
        return len(self.cell_types)

    @property
    def is_deterministic(self) -> bool:
        return self.rule.is_deterministic

    def index_of(self, name: str) -> int:
        for i, c in enumerate(self.cell_types):
            if c.name == name:
                return i
        raise KeyError(f"unknown cell type {name!r}")

    def check_grid(self, grid: Grid) -> None:
        if grid.ndim != self.neighborhood.ndim:
            raise ValueError(f"grid is {grid.ndim}D but the neighbourhood is {self.neighborhood.ndim}D")
        if grid.cells.size and int(grid.cells.max()) >= self.num_states:
            raise ValueError(f"grid contains states >= {self.num_states}")

    def step(self, grid: Grid, rng: np.random.Generator | None = None) -> Grid:
        """One synchronous update: every cell is computed from the old grid.

        Stochastic automata need `rng`. Create it once per run with
        np.random.default_rng(seed) and pass the same generator to every step;
        the same seed then reproduces the same run. Deterministic automata ignore it.
        """
        if rng is None and not self.is_deterministic:
            raise ValueError(f"automaton {self.name!r} is stochastic; step() needs an rng")
        self.check_grid(grid)
        cells = grid.cells
        padded = self.boundary.pad(cells, self.neighborhood.reach)
        neighbours = self.neighborhood.gather(padded, cells.shape)
        return Grid(self.rule.apply(cells, neighbours, self.num_states, rng))

    def __repr__(self) -> str:
        return (f"Automaton({self.name!r}, states={[c.name for c in self.cell_types]}, "
                f"{self.neighborhood!r}, {self.boundary!r}, {type(self.rule).__name__})")