"""Totalistic rules: only the current state and a neighbour count matter."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from .base import Rule, check_state


@dataclass(frozen=True)
class TotalisticRule(Rule):
    """next = transitions[current][count of neighbours in state `counted`].

    Combinations not listed go to `default`, or stay unchanged if default is None.
    Game of Life: counted=1, transitions={0: {3: 1}, 1: {2: 1, 3: 1}}, default=0.
    """

    counted: int
    transitions: Mapping[int, Mapping[int, int]]
    default: int | None = None

    def __post_init__(self) -> None:
        normalised = {int(cur): {int(c): int(n) for c, n in m.items()} for cur, m in self.transitions.items()}
        object.__setattr__(self, "transitions", normalised)

    def validate(self, num_states: int, neighborhood_size: int) -> None:
        check_state(self.counted, num_states, "counted state")
        if self.default is not None:
            check_state(self.default, num_states, "default")
        for cur, mapping in self.transitions.items():
            check_state(cur, num_states, "current state")
            for count, nxt in mapping.items():
                if not 0 <= count <= neighborhood_size:
                    raise ValueError(f"count {count} is impossible with {neighborhood_size} neighbours")
                check_state(nxt, num_states, "next state")

    def apply(self, current, neighbours, num_states, rng=None) -> np.ndarray:
        counts = np.count_nonzero(neighbours == self.counted, axis=-1)
        out = current.copy() if self.default is None else np.full_like(current, self.default)
        for cur, mapping in self.transitions.items():
            is_cur = current == cur
            for count, nxt in mapping.items():
                out[is_cur & (counts == count)] = nxt
        return out