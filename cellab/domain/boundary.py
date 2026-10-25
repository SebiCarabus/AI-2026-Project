"""Boundaries: what a cell sees beyond the edge of the grid."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


class Boundary(ABC):
    @abstractmethod
    def pad(self, cells: np.ndarray, width: int) -> np.ndarray:
        """Return `cells` extended by `width` cells on every side of every axis."""


@dataclass(frozen=True)
class Periodic(Boundary):
    """Edges wrap around (a ring in 1D, a torus in 2D)."""

    def pad(self, cells: np.ndarray, width: int) -> np.ndarray:
        return np.pad(cells, width, mode="wrap")


@dataclass(frozen=True)
class Fixed(Boundary):
    """Everything outside the grid is permanently `value`."""

    value: int = 0

    def pad(self, cells: np.ndarray, width: int) -> np.ndarray:
        return np.pad(cells, width, mode="constant", constant_values=self.value)


@dataclass(frozen=True)
class Reflect(Boundary):
    """The grid is mirrored at its edges (the edge cell is repeated)."""

    def pad(self, cells: np.ndarray, width: int) -> np.ndarray:
        return np.pad(cells, width, mode="symmetric")