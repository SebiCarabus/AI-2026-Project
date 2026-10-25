# `domain` — the cellular automaton model

This package holds the core model of CelLab: grids, neighbourhoods, boundaries, rules and the
`Automaton` that combines them. It is pure Python + NumPy, with no files, YAML, database or web code.

The rest of the project builds on it:

```
spec/            YAML / Pydantic  ──builds──▶  domain objects
services/        Simulator, queries ──calls──▶  Automaton.step()
infrastructure/  storage, file formats ──stores──▶ Grid
web/             API / UI
```

**Rule: `domain/` never imports from `spec`, `services`, `infrastructure` or `web`.** Dependencies only point
inwards. If you need something from another layer inside `domain`, it probably belongs in that other layer.

---

## Contents

1. [Folder structure](#folder-structure)
2. [Core idea](#core-idea)
3. [Quick start](#quick-start)
4. [The classes](#the-classes)
5. [Building automata by hand](#building-automata-by-hand)
6. [Randomness and reproducibility](#randomness-and-reproducibility)
7. [Conventions and gotchas](#conventions-and-gotchas)
8. [Adding a new kind of neighbourhood / boundary / rule](#extending)
9. [Running the tests](#running-the-tests)

---

## Folder structure

```
domain/
├── grid.py            Grid (immutable board), CellType
├── neighborhood.py    Neighborhood (abstract), Moore, VonNeumann, Line, Custom
├── boundary.py        Boundary (abstract), Periodic, Fixed, Reflect
├── automaton.py       Automaton: cell types + neighbourhood + boundary + rule
├── presets.py         Ready-made automata: elementary / rule_30, cyclic, game_of_life, forest_fire
└── rules/
    ├── base.py        Rule (abstract), check_state helper
    ├── lookup.py      LookupTableRule (+ from_wolfram)
    ├── totalistic.py  TotalisticRule
    ├── conditional.py ConditionalRule, Branch, Conditions, Targets (incl. random ones)
    └── factories.py   Helpers that build common rules (cyclic_rule)
```

Imports you will normally need:

```python
from domain import Automaton, CellType, Grid
from domain import Moore, VonNeumann, Line, Custom          # neighbourhoods
from domain import Periodic, Fixed, Reflect                 # boundaries
from domain.rules import (
    LookupTableRule, TotalisticRule, ConditionalRule, Branch,
    CellIs, CountInRange, WithProbability,                  # conditions
    SetTo, Identity, RandomChoice,                          # targets
)
from domain import presets
```

---

## Core idea

An automaton is **not** a class of its own. It is built from four interchangeable parts:

| Part | Question it answers | Examples |
|---|---|---|
| `CellType`s | What states can a cell be in? | `dead`, `alive` |
| `Neighborhood` | Which cells does a cell look at, and in what order? | `Moore(1)`, `Line(1)` |
| `Boundary` | What is outside the edge of the grid? | `Periodic()`, `Fixed(0)` |
| `Rule` | Given a cell and its neighbours, what does it become? | `LookupTableRule.from_wolfram(30)` |

Rule 30 and the cyclic automaton are two **configurations** of these parts. New behaviour means a new
subclass of one part, not a new automaton class.

### What happens in one `step()`

```
grid ──▶ Boundary.pad ──▶ Neighborhood.gather ──▶ Rule.apply ──▶ new Grid
          (add a border)    (one neighbour vector    (next value
                             per cell)                per cell)
```

1. The boundary pads the grid so edge cells also have neighbours.
2. The neighbourhood builds, for **every** cell at once, an ordered vector of its neighbours' values.
   Result shape: `(*grid.shape, neighbourhood.size)`.
3. The rule turns `(current values, neighbour vectors)` into next values.
4. A new `Grid` is returned. The old one is unchanged.

All cells are updated **synchronously** from the old grid. Rules never see coordinates or the number of
dimensions, so the same rule classes work in 1D, 2D or 3D.

---

## Quick start

```python
from domain import Grid
from domain.presets import rule_30, cyclic

# Rule 30 from a single live cell
a = rule_30()
g = Grid.from_text("0000001000000")
for _ in range(4):
    print(g.to_text())
    g = a.step(g)
# 0000001000000
# 0000011100000
# 0000110010000
# 0001101111000

# Cyclic automaton with 4 states (von Neumann neighbours, wrap-around edges)
c = cyclic(num_states=4, threshold=2)
g = Grid.from_text("""
0011
0221
3321
""")
print(c.step(g).to_text())
# 0021
# 0221
# 0321
```

A stochastic automaton needs a random generator (see [Randomness](#randomness-and-reproducibility)):

```python
import numpy as np
from domain import Grid
from domain.presets import forest_fire

a = forest_fire(growth=0.05, lightning=0.001)
rng = np.random.default_rng(seed=42)        # once per run
g = Grid(np.zeros((50, 50), dtype=int))
for _ in range(100):
    g = a.step(g, rng)
print(g.count(1), "trees")
```

### Available presets (`domain/presets.py`)

| Function | Automaton | Defaults |
|---|---|---|
| `elementary(number, boundary=None)` | Any of the 256 elementary 1D rules | `Line(1)`, `Fixed(0)` |
| `rule_30(boundary=None)` | Rule 30 | same as above |
| `cyclic(num_states=4, threshold=2, neighborhood=None, boundary=None)` | Cyclic CA | `VonNeumann(1)`, `Periodic()` |
| `game_of_life(boundary=None)` | Conway's Life | `Moore(1)`, `Periodic()` |
| `forest_fire(growth=0.05, lightning=0.001, neighborhood=None, boundary=None)` | Drossel–Schwabl forest fire (**stochastic**) | `Moore(1)`, `Fixed(0)` |

---

## The classes

The constructor parameters below are the **contract** for whoever builds these objects from a spec.

### `Grid` and `CellType` (`grid.py`)

| | |
|---|---|
| `CellType(name)` | A named state. Its integer value is its **position** in `Automaton.cell_types`. |
| `Grid(cells)` | Immutable n-dimensional array of state indices. Accepts lists or NumPy arrays of ints/bools. Always copies its input. |
| `grid.cells` | Read-only NumPy array (`uint8`). Writing to it raises an error. |
| `grid.shape`, `grid.ndim` | As in NumPy. |
| `grid.hash()` | Stable content hash (hex string). Same board → same hash on every machine. Used for cycle detection. |
| `grid == other`, `hash(grid)` | Content equality, so grids can be dict keys and set members. |
| `grid.count(state)` | Number of cells in `state`. |
| `grid.counts(num_states)` | Array with the count of every state. |
| `Grid.from_text(s)` / `grid.to_text()` | One line of digits → 1D, several lines → 2D. Only states 0–9. Useful for tests and debugging. |

### Neighbourhoods (`neighborhood.py`)

| Class | Parameters | Cells | Order of the neighbour vector |
|---|---|---|---|
| `Moore` | `radius=1, dimensions=2, include_center=False` | 8 in 2D for radius 1 | row-major: `(-1,-1), (-1,0), (-1,1), (0,-1), (0,1), (1,-1), (1,0), (1,1)` |
| `VonNeumann` | `radius=1, dimensions=2, include_center=False` | 4 in 2D for radius 1 | row-major: up, left, right, down |
| `Line` | `radius=1, include_center=True` | 3 for radius 1 | left to right: `(left, self, right)` |
| `Custom` | `points` (tuple of offset tuples) | any | exactly as given |

Useful attributes: `offsets()`, `size` (number of neighbours), `ndim`, `reach` (how far the boundary must pad).

Offsets are `(row, column)` in 2D. The neighbourhood's `ndim` must match the grid's.

### Boundaries (`boundary.py`)

| Class | Parameters | Meaning |
|---|---|---|
| `Periodic()` | – | Edges wrap around (ring in 1D, torus in 2D). |
| `Fixed(value=0)` | `value` | Everything outside the grid is permanently `value`. |
| `Reflect()` | – | Mirrored at the edges (the edge cell is repeated). |

### Rules (`rules/`)

There are four rule families. The fourth one, probabilistic, is a `ConditionalRule` with random parts.

#### `LookupTableRule(table, default=None)`

The exact **ordered** neighbour pattern decides the next state.

- `table`: `{pattern tuple: next state}`. Each pattern has one value per neighbour, in the neighbourhood's order.
- `default`: state for patterns missing from the table. If `None`, the table must be complete.
- `LookupTableRule.from_wolfram(number, radius=1)` builds an elementary rule from its Wolfram number.

To make the cell's own value part of the pattern, use a neighbourhood that includes the centre (`Line(1)` does by default).

#### `TotalisticRule(counted, transitions, default=None)`

Only the cell's current state and **how many** neighbours are in state `counted` matter.

- `transitions`: `{current state: {count: next state}}`.
- `default`: state for combinations not listed. `None` means "stay unchanged".

```python
# Game of Life: born with 3, survives with 2 or 3, otherwise dead
TotalisticRule(counted=1, transitions={0: {3: 1}, 1: {2: 1, 3: 1}}, default=0)
```

#### `ConditionalRule(branches, otherwise)`

An ordered list of `Branch(condition, target)`. **The first matching branch wins.** Cells that match no
branch use `otherwise`, which is required, so every cell always gets a next state.

**Conditions** (combine them with `&`, `|`, `~`):

| Condition | Parameters | True when |
|---|---|---|
| `CellIs(state)` | | the cell itself is in `state` |
| `CountInRange(state, minimum=None, maximum=None)` | bounds inclusive, one may be `None` | `minimum ≤ neighbours in state ≤ maximum` |
| `CountInRange.at_least(state, n)` / `.at_most(...)` / `.exactly(...)` | | shortcuts for the above |
| `WithProbability(p)` | `0 ≤ p ≤ 1` | randomly, independently per cell, with probability `p` |
| `And(parts)`, `Or(parts)`, `Not(part)` | | combinations (what `&`, `\|`, `~` produce) |

**Targets**:

| Target | Parameters | The cell becomes |
|---|---|---|
| `SetTo(state)` | | `state` |
| `Identity()` | | itself (no change) |
| `RandomChoice({state: probability, ...})` | probabilities sum to 1 | a random state |

### `Automaton` (`automaton.py`)

```python
Automaton(name, cell_types, neighborhood, boundary, rule)
```

| Member | |
|---|---|
| `step(grid, rng=None) -> Grid` | One synchronous update. Pure: does not change the automaton or the input grid. |
| `num_states` | `len(cell_types)` |
| `is_deterministic` | `False` if the rule contains anything random. |
| `index_of(name)` | Cell type name → integer state, e.g. `a.index_of("alive") == 1`. |
| `check_grid(grid)` | Raises `ValueError` if the grid has the wrong number of dimensions or invalid states. |

**The constructor validates everything** and raises `ValueError` with a readable message. For example:
- duplicate cell type names
- a lookup pattern with the wrong length
- a state index that doesn't exist
- a count that can never happen with this neighbourhood
- probabilities outside `[0, 1]`, or a `RandomChoice` that doesn't sum to 1

If an `Automaton` was constructed successfully, it is guaranteed to be usable. The spec layer can pass
these messages on to the user.

---

## Building automata by hand

These are the patterns to follow when turning a spec into objects.

**Lookup table (any elementary rule):**

```python
from domain import Automaton, CellType, Line, Fixed
from domain.rules import LookupTableRule

rule_110 = Automaton(
    name="rule-110",
    cell_types=[CellType("dead"), CellType("alive")],
    neighborhood=Line(1),
    boundary=Fixed(0),
    rule=LookupTableRule.from_wolfram(110),
)
```

**Conditional (Game of Life written as rules, like in the spec format):**

```python
from domain import Automaton, CellType, Moore, Periodic
from domain.rules import ConditionalRule, Branch, CellIs, CountInRange, SetTo

DEAD, ALIVE = 0, 1
life = Automaton(
    name="life-conditional",
    cell_types=[CellType("dead"), CellType("alive")],
    neighborhood=Moore(1),
    boundary=Periodic(),
    rule=ConditionalRule(
        [
            Branch(CellIs(ALIVE) & CountInRange(ALIVE, 2, 3), SetTo(ALIVE)),
            Branch(CellIs(DEAD) & CountInRange.exactly(ALIVE, 3), SetTo(ALIVE)),
        ],
        otherwise=SetTo(DEAD),
    ),
)
```

This maps one-to-one onto the YAML rules: each `when` becomes a `Branch` and `otherwise` becomes `otherwise`.
Cell type **names** from the spec become integers with their position in the `cell_types` list.

**Probabilistic (random parts inside a conditional rule):**

```python
from domain import Automaton, CellType, Moore, Fixed
from domain.rules import ConditionalRule, Branch, CellIs, CountInRange, WithProbability, SetTo, Identity

EMPTY, TREE, BURNING = 0, 1, 2
fire = Automaton(
    name="forest-fire",
    cell_types=[CellType("empty"), CellType("tree"), CellType("burning")],
    neighborhood=Moore(1),
    boundary=Fixed(EMPTY),
    rule=ConditionalRule(
        [
            Branch(CellIs(BURNING), SetTo(EMPTY)),
            Branch(CellIs(TREE) & CountInRange.at_least(BURNING, 1), SetTo(BURNING)),
            Branch(CellIs(TREE) & WithProbability(0.001), SetTo(BURNING)),
            Branch(CellIs(EMPTY) & WithProbability(0.05), SetTo(TREE)),
        ],
        otherwise=Identity(),
    ),
)
assert not fire.is_deterministic
```

If a random condition fails, the cell falls through to the next branch.

---

## Randomness and reproducibility

- `step(grid, rng=None)` takes an optional `numpy.random.Generator`.
- **Deterministic automata ignore `rng`**, so `a.step(g)` is fine.
- **Stochastic automata raise `ValueError` if `rng` is missing.** This is deliberate: it prevents runs that can't be reproduced.
- Create the generator **once per run** and reuse it for every step:

```python
import numpy as np
from domain import Grid
from domain.presets import forest_fire

def run(automaton, grid, steps, seed):
    rng = np.random.default_rng(seed)       # once, at the start of the run
    for _ in range(steps):
        grid = automaton.step(grid, rng)    # same generator every step
    return grid

a = forest_fire(growth=0.3)
g = Grid(np.zeros((20, 20), dtype=int))
assert run(a, g, 10, seed=1) == run(a, g, 10, seed=1)   # same seed → same run
```

Do **not** create a new generator inside the loop. Always store the seed with a run's results.

`is_deterministic` matters for queries. For a deterministic automaton on a finite grid, the first repeated
`grid.hash()` proves a cycle or a stable state. For a stochastic one it doesn't, so query answers have to say so.

---

## Conventions and gotchas

- **States are integers:** the index of the cell type in `cell_types`. Names, symbols and colours are not stored in the grid.
- **At most 256 states**, because grids are stored as `uint8`.
- **The grid's dimensions must match the neighbourhood's.** `Line` is 1D, `Moore` / `VonNeumann` are 2D by default, and `dimensions=3` gives 3D.
- **Neighbour order matters only for `LookupTableRule`.** Count-based conditions and `TotalisticRule` ignore it.
- **The centre cell is excluded** from `Moore` / `VonNeumann` by default, so counts are over the *other* cells. `Line` includes the centre by default, because elementary rules need `(left, self, right)`.
- **Grids are immutable.** `step()` returns a new grid, and `grid.cells` is read-only. Use `grid.cells.copy()` if you need to edit an array, then create a new `Grid`.
- **Rules and neighbourhoods are frozen dataclasses**: create a new one instead of modifying.
- **`Fixed` is a boundary; `SetTo` is a rule target.** Don't confuse them.

---

## Extending

To add new behaviour, subclass one part and implement its abstract methods:

| To add a new… | Subclass | Implement | Optionally override |
|---|---|---|---|
| neighbourhood | `Neighborhood` | `offsets()` | – |
| boundary | `Boundary` | `pad(cells, width)` | – |
| rule family | `Rule` | `apply(current, neighbours, num_states, rng=None)` | `validate(...)`, `is_deterministic` |
| condition | `Condition` | `mask(current, neighbours, rng=None)` | `validate(...)`, `is_deterministic` |
| target | `Target` | `resolve(current, neighbours, rng=None)` | `validate(...)`, `is_deterministic` |

Guidelines:
- Work on whole arrays (vectorised NumPy), not cell by cell.
- `current` has the grid's shape. `neighbours` has the same shape plus one last axis of length `neighbourhood.size`.
- Put every check in `validate` and raise `ValueError` with a clear message. `Automaton` calls it at construction.
- Anything random must use the `rng` argument (never `np.random.*` directly) and report `is_deterministic = False`.
- Add a preset to `presets.py` and a test.

Prefer composing existing parts over adding classes. The cyclic automaton, for example, is a normal
`ConditionalRule` generated by `rules/factories.py:cyclic_rule`.

---

## Running the tests

Tests live in `cellab/tests/domain/`. Run them from inside `cellab`:

```
cd cellab
python -m pytest
```

Use `python -m pytest`, not plain `pytest`, so that `domain` can be imported. In PyCharm, with `cellab`
marked as Sources Root, the run button works too.

Requirements: `numpy`, `pytest`, `hypothesis`.

Don't add an `__init__.py` to `tests/domain/`: the folder would then hide the real `domain` package.
