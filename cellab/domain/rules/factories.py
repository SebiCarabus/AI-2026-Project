"""Helpers that build common rules out of the generic families."""
from __future__ import annotations

from .conditional import Branch, CellIs, ConditionalRule, CountInRange, SetTo, Identity


def cyclic_rule(num_states: int, threshold: int) -> ConditionalRule:
    """Cyclic CA: a cell in state x moves to (x+1) mod n if at least `threshold`
    neighbours are already in (x+1) mod n; otherwise it stays.

    Expanded into one ordinary branch per state, so no special rule class is needed.
    """
    if num_states < 2:
        raise ValueError("a cyclic automaton needs at least 2 states")
    branches = []
    for x in range(num_states):
        nxt = (x + 1) % num_states
        branches.append(Branch(CellIs(x) & CountInRange.at_least(nxt, threshold), SetTo(nxt)))
    return ConditionalRule(branches, otherwise=Identity())