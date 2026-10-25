"""The abstract Rule: what a cell becomes, given itself and its neighbours."""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class Rule(ABC):
    """Maps (current values, neighbour vectors) to next values, for all cells at once.

    Rules never see coordinates or the grid's dimensionality; the Automaton does the
    padding and gathering, so the same rule works in 1D, 2D or 3D.
    """

    @property
    def is_deterministic(self) -> bool:
        """False if the rule uses randomness. Queries rely on this flag."""
        return True

    @abstractmethod
    def apply(
        self,
        current: np.ndarray,
        neighbours: np.ndarray,
        num_states: int,
        rng: np.random.Generator | None = None,
    ) -> np.ndarray:
        """
        current:    shape S, the cells' present states
        neighbours: shape (*S, k), each cell's ordered neighbour values
        rng:        source of randomness; deterministic rules ignore it
        returns:    shape S, the next states
        """

    def validate(self, num_states: int, neighborhood_size: int) -> None:
        """Raise ValueError if the rule can't work with this automaton. Override as needed."""


def check_state(value: int, num_states: int, what: str) -> None:
    if not 0 <= value < num_states:
        raise ValueError(f"{what} {value} is not a valid state (0..{num_states - 1})")