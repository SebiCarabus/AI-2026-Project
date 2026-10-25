"""Lookup-table rules: the exact ordered neighbour pattern decides the next state."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

from .base import Rule, check_state

Pattern = tuple[int, ...]


@dataclass(frozen=True)
class LookupTableRule(Rule):
    """next = table[neighbour vector].

    The pattern is read in the neighbourhood's offset order; to make the cell's own
    value part of the key, use a neighbourhood that includes the centre (e.g. Line(1)).
    `default` is used for patterns missing from the table; if it is None, the table
    must be complete.
    """

    table: Mapping[Pattern, int]
    default: int | None = None
    _dense: dict = field(default_factory=dict, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        normalised = {tuple(int(v) for v in k): int(v) for k, v in self.table.items()}
        object.__setattr__(self, "table", normalised)

    @classmethod
    def from_wolfram(cls, number: int, radius: int = 1) -> LookupTableRule:
        """Elementary (radius 1) or wider binary 1D rules from their Wolfram number.

        Pattern (l, c, r) read as a binary number n; next state is bit n of `number`.
        """
        k = 2 * radius + 1
        n_patterns = 2 ** k
        if not 0 <= number < 2 ** n_patterns:
            raise ValueError(f"Wolfram number must be in [0, {2 ** n_patterns})")
        table = {
            tuple((code >> (k - 1 - i)) & 1 for i in range(k)): (number >> code) & 1
            for code in range(n_patterns)
        }
        return cls(table)

    def validate(self, num_states: int, neighborhood_size: int) -> None:
        for pattern, result in self.table.items():
            if len(pattern) != neighborhood_size:
                raise ValueError(
                    f"pattern {pattern} has length {len(pattern)}, "
                    f"but the neighbourhood has {neighborhood_size} cells"
                )
            for v in pattern:
                check_state(v, num_states, f"value in pattern {pattern}:")
            check_state(result, num_states, f"result of pattern {pattern}:")
        if self.default is None:
            if len(self.table) < num_states ** neighborhood_size:
                raise ValueError("lookup table is incomplete and no default was given")
        else:
            check_state(self.default, num_states, "default")

    def _dense_table(self, num_states: int, k: int) -> np.ndarray:
        key = (num_states, k)
        if key not in self._dense:
            dense = np.full(num_states ** k, -1 if self.default is None else self.default, dtype=np.int64)
            weights = num_states ** np.arange(k - 1, -1, -1)
            for pattern, result in self.table.items():
                dense[int(np.dot(pattern, weights))] = result
            self._dense[key] = dense
        return self._dense[key]

    def apply(self, current, neighbours, num_states, rng=None) -> np.ndarray:
        k = neighbours.shape[-1]
        weights = num_states ** np.arange(k - 1, -1, -1, dtype=np.int64)
        codes = neighbours.astype(np.int64) @ weights  # pattern -> one integer per cell
        return self._dense_table(num_states, k)[codes]