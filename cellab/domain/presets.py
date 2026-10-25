"""Ready-made automata, assembled from the generic parts."""
from __future__ import annotations

from .automaton import Automaton
from .boundary import Boundary, Fixed, Periodic
from .grid import CellType
from .neighborhood import Line, Moore, Neighborhood, VonNeumann
from .rules import (
    Branch, CellIs, ConditionalRule, CountInRange, Identity, LookupTableRule, SetTo,
    TotalisticRule, WithProbability, cyclic_rule,
)

DEAD_ALIVE = (CellType("dead"), CellType("alive"))


def elementary(number: int, boundary: Boundary | None = None) -> Automaton:
    """Any of the 256 elementary 1D automata. Cells outside the line are dead by default."""
    return Automaton(
        name=f"rule-{number}",
        cell_types=DEAD_ALIVE,
        neighborhood=Line(1),  # (left, self, right)
        boundary=boundary or Fixed(0),
        rule=LookupTableRule.from_wolfram(number),
    )


def rule_30(boundary: Boundary | None = None) -> Automaton:
    return elementary(30, boundary)


def cyclic(
    num_states: int = 4,
    threshold: int = 2,
    neighborhood: Neighborhood | None = None,
    boundary: Boundary | None = None,
) -> Automaton:
    """2D cyclic CA. Defaults: von Neumann neighbourhood, wrap-around edges."""
    return Automaton(
        name=f"cyclic-{num_states}",
        cell_types=tuple(CellType(f"s{i}") for i in range(num_states)),
        neighborhood=neighborhood or VonNeumann(1),
        boundary=boundary or Periodic(),
        rule=cyclic_rule(num_states, threshold),
    )


def game_of_life(boundary: Boundary | None = None) -> Automaton:
    return Automaton(
        name="conway-life",
        cell_types=DEAD_ALIVE,
        neighborhood=Moore(1),
        boundary=boundary or Periodic(),
        rule=TotalisticRule(counted=1, transitions={0: {3: 1}, 1: {2: 1, 3: 1}}, default=0),
    )

def forest_fire(
    growth: float = 0.05,
    lightning: float = 0.001,
    neighborhood: Neighborhood | None = None,
    boundary: Boundary | None = None,
) -> Automaton:
    """Drossel-Schwabl forest fire (stochastic). States: empty, tree, burning.

    burning -> empty; tree next to fire -> burning; tree struck by lightning -> burning;
    empty -> tree with probability `growth`.
    """
    EMPTY, TREE, BURNING = 0, 1, 2
    rule = ConditionalRule(
        [
            Branch(CellIs(BURNING), SetTo(EMPTY)),
            Branch(CellIs(TREE) & CountInRange.at_least(BURNING, 1), SetTo(BURNING)),
            Branch(CellIs(TREE) & WithProbability(lightning), SetTo(BURNING)),
            Branch(CellIs(EMPTY) & WithProbability(growth), SetTo(TREE)),
        ],
        otherwise=Identity(),
    )
    return Automaton(
        name="forest-fire",
        cell_types=(CellType("empty"), CellType("tree"), CellType("burning")),
        neighborhood=neighborhood or Moore(1),
        boundary=boundary or Fixed(0),
        rule=rule,
    )