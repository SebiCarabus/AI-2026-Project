# CelLab YAML Parser

`cellab/parser` reads a `*.cell.yaml` document, validates it, and compiles it into the
objects in `cellab/domain`, mainly `Automaton` and `Grid`.

The parser is intentionally strict. Unknown keys, duplicate YAML keys, unknown cell
types, impossible neighbour counts, and out-of-bounds initial cells are rejected with
a path such as `rules[0].when.count.alive`.

## Quick start

Run this from `cellab/` so the existing top-level `domain` and `parser` imports work:

```python
from parser import load_spec

spec = load_spec("parser/examples/conway-life.cell.yaml")
automaton = spec.compile()
grid = spec.build_initial_state()

print(automaton)
print(grid.to_text())
```

You can also parse a string directly:

```python
from parser import parse_spec

spec = parse_spec("""
schema_version: 1
name: example
world: {size: [10], boundary: periodic}
neighborhood: {type: line, radius: 1}
cell_types:
  dead: {symbol: ".", color: "#ffffff"}
  alive: {symbol: "#", color: "#111111"}
rules:
  - otherwise: dead
""")
```

## Public API

| Function or type | Purpose |
|---|---|
| `parse_spec(text)` | Parse and validate one YAML document. Returns `AutomatonSpec`. |
| `load_spec(path)` | Read a UTF-8 YAML file and call `parse_spec`. |
| `AutomatonSpec.compile()` | Validate and build `Automaton`. |
| `AutomatonSpec.build_initial_state()` | Build the optional initial `Grid`, or return `None`. |
| `SpecError` | Raised for any parse, validation, or compilation problem. |
| `SpecIssue` | One problem with a `path` and a `message`. |

`SpecError` is a `ValueError`. Its `.issues` attribute is a list of `SpecIssue`
objects:

```python
from parser import SpecError, parse_spec

try:
    parse_spec("""
schema_version: 1
name: bad
world: {size: [5, 5], boundary: torus}
neighborhood: {type: moore, radius: 1}
cell_types:
  dead: {symbol: ".", color: "#ffffff"}
  alive: {symbol: "#", color: "#111111"}
rules:
  - when: {cell: aliv}
    then: alive
  - otherwise: dead
""")
except SpecError as error:
    for issue in error.issues:
        print(issue.path, issue.message)
```

Output:

```text
rules[0].when.cell unknown cell type 'aliv'
```

## Processing pipeline

```text
YAML text
  -> safe YAML loader with duplicate-key detection
  -> Pydantic models (AutomatonSpec and friends)
  -> semantic validation
  -> compile to domain Automaton
  -> optional build of the initial Grid
```

| Module | Responsibility |
|---|---|
| `parser.py` | YAML loading, duplicate-key detection, Pydantic error formatting, `parse_spec`, `load_spec`. |
| `models.py` | Typed Pydantic intermediate representation and semantic property helpers. |
| `compiler.py` | Cross-field semantic validation and conversion to `domain` objects. |
| `errors.py` | `SpecIssue` and `SpecError`. |
| `examples/` | Four complete, tested specs. |

## Spec grammar

A document describes exactly one automaton.

| Key | Required | Meaning |
|---|---|---|
| `schema_version` | yes | Must be `1`. |
| `name` | yes | Automaton name. |
| `world` | yes | Grid size and boundary. |
| `neighborhood` | yes | Which cells count as neighbours. |
| `cell_types` | yes | Cell state metadata. The mapping order defines state indices. |
| `rules` | one of | Ordered conditional/probabilistic branches. |
| `rule` | one of | Totalistic or lookup-table family. |
| `initial_state` | no | Initial board. |

Exactly one of `rules` and `rule` must be present. Unknown keys are rejected at every
level.

### World and coordinates

```yaml
world:
  size: [64, 64]
  boundary: torus
```

- `world.size: [width, height]` describes a 2D world. The NumPy grid shape is
  `(height, width)`.
- `world.size: [length]` describes a 1D world. The grid shape is `(length,)`.
- Only 1D and 2D worlds are accepted.
- Initial-state coordinates are `[row, col]` for 2D and `[index]` for 1D.

Boundary forms:

| YAML | Domain class |
|---|---|
| `torus` or `periodic` | `Periodic()` |
| `reflect` | `Reflect()` |
| `{type: fixed, value: dead}` | `Fixed(index_of_dead)` |

### Cell types

```yaml
cell_types:
  dead:  {symbol: ".", color: "#ffffff"}
  alive: {symbol: "#", color: "#111111"}
```

- The order defines integer state indices. Here `dead` is `0` and `alive` is `1`.
- Every cell type needs a unique one-character non-whitespace `symbol`.
- `color` must match `^#[0-9a-fA-F]{6}$`.
- A spec must declare between 2 and 256 cell types.

### Neighbourhoods

```yaml
neighborhood:
  type: moore
  radius: 1
```

| `type` | Domain class | Notes |
|---|---|---|
| `moore` | `Moore(radius, dimensions)` | Square-shaped neighbourhood, excluding the centre. |
| `von_neumann` | `VonNeumann(radius, dimensions)` | Manhattan-distance neighbourhood. |
| `line` | `Line(radius)` | 1D only. Includes the centre, so rule 110 patterns are `[left, centre, right]`. |
| `custom` | `Custom(points)` | Explicit offsets such as `points: [[-1, 0], [1, 0]]`. |

`radius` defaults to `1`. `dimensions` is optional for `moore` and `von_neumann`; it is
inferred from the world. A mismatch between the world and the neighbourhood is an
error.

### Conditional and probabilistic rules

`rules` is an ordered list. Branches are tried top to bottom and the first match wins.
Exactly one `otherwise` branch is required, and it must be last.

```yaml
rules:
  - when: {cell: alive, count: {alive: [2, 3]}}
    then: alive
  - when: {cell: dead, count: {alive: 3}}
    then: alive
  - otherwise: dead
```

`when` accepts these parts; all present parts are ANDed:

| Part | Meaning | Domain condition |
|---|---|---|
| `cell` | The current cell has this state. | `CellIs` |
| `count` | The neighbourhood contains a number of cells in the named state. | `CountInRange` |
| `probability` | A random gate. | `WithProbability` |

`count` values are either an exact integer or `[min, max]`. Either bound may be `null`,
and bounds must be non-negative and possible for the declared neighbourhood.

Targets:

| YAML | Domain target |
|---|---|
| `alive` | `SetTo(index_of_alive)` |
| `same` | `Identity()` |
| `{random: {alive: 0.7, dead: 0.3}}` | `RandomChoice({1: 0.7, 0: 0.3})` |

Random probabilities must sum to `1`. Probability conditions and random targets can
make the automaton stochastic; when `is_deterministic` is `False`,
`Automaton.step(grid)` requires an `rng`.

### Totalistic rules

A totalistic rule depends only on the current state and the number of neighbours in
`counted`.

```yaml
rule:
  type: totalistic
  counted: alive
  transitions:
    dead:  {3: alive}
    alive: {2: alive, 3: alive}
  default: dead
```

This compiles to:

```python
TotalisticRule(
    counted=1,
    transitions={0: {3: 1}, 1: {2: 1, 3: 1}},
    default=0,
)
```

`default` is optional. If it is omitted, cells stay unchanged for combinations that
are not listed.

### Lookup-table rules

A lookup rule maps a complete neighbourhood pattern to the next state.

```yaml
rule:
  type: lookup
  table:
    - {pattern: [dead, dead, dead], then: dead}
    - {pattern: [dead, dead, alive], then: alive}
  default: dead
```

Patterns are read in the neighbourhood's offset order. For `line` with radius `1`,
the order is left, centre, right. `default` is optional; if it is omitted, the table
must contain every possible pattern.

### Initial state

`initial_state` is optional.

```yaml
initial_state:
  fill: dead
  cells:
    alive: [[3, 3], [3, 4], [3, 5]]
```

- `fill` is optional and defaults to the first declared cell type.
- `cells` maps a cell type name to a list of coordinates.
- Coordinates must be inside the grid and may not be assigned twice.
- If `initial_state` is omitted, `build_initial_state()` returns `None`.

## Examples and their compiled classes

All four examples live in `examples/`.

| Example | Rule class | Neighbourhood | Boundary | Initial grid |
|---|---|---|---|---|
| `conway-life.cell.yaml` | `ConditionalRule` | `Moore(1, 2)` | `Periodic()` | `(8, 8)` |
| `forest-fire.cell.yaml` | `ConditionalRule` | `Moore(1, 2)` | `Fixed(0)` | `(32, 32)` |
| `life-totalistic.cell.yaml` | `TotalisticRule` | `Moore(1, 2)` | `Periodic()` | `(8, 8)` |
| `rule-110.cell.yaml` | `LookupTableRule` | `Line(1)` | `Fixed(0)` | `(20,)` |

Every example compiles to the same outer class, `Automaton`, with different
neighbourhood, boundary, and rule objects inside it.

### Conway's Game of Life

`examples/conway-life.cell.yaml`:

```yaml
schema_version: 1
name: conway-life

world:
  size: [8, 8]
  boundary: torus

neighborhood:
  type: moore
  radius: 1

cell_types:
  dead:
    symbol: "."
    color: "#ffffff"
  alive:
    symbol: "#"
    color: "#111111"

rules:
  - when: {cell: alive, count: {alive: [2, 3]}}
    then: alive
  - when: {cell: dead, count: {alive: 3}}
    then: alive
  - otherwise: dead

initial_state:
  fill: dead
  cells:
    alive: [[3, 3], [3, 4], [3, 5]]
```

Compiled automaton:

```python
Automaton('conway-life', states=['dead', 'alive'], Moore(radius=1, dimensions=2, include_center=False), Periodic(), ConditionalRule)
```

The rule is a deterministic `ConditionalRule` with two branches:

1. A live cell with 2 or 3 live neighbours survives (`alive`).
2. A dead cell with exactly 3 live neighbours becomes alive (`alive`).

Everything else uses `otherwise: dead`. The initial grid is an 8 by 8 blinker at row 3,
columns 3 through 5.

### Forest Fire

`examples/forest-fire.cell.yaml`:

```yaml
schema_version: 1
name: forest-fire

world:
  size: [32, 32]
  boundary: {type: fixed, value: empty}

neighborhood:
  type: moore
  radius: 1

cell_types:
  empty:
    symbol: "."
    color: "#f5f5dc"
  tree:
    symbol: "T"
    color: "#228b22"
  burning:
    symbol: "*"
    color: "#ff4500"

rules:
  - when: {cell: burning}
    then: empty
  - when: {cell: tree, count: {burning: [1, null]}}
    then: burning
  - when: {cell: tree, probability: 0.001}
    then: burning
  - when: {cell: empty, probability: 0.05}
    then: tree
  - otherwise: same

initial_state:
  fill: empty
  cells:
    tree: [[5, 5], [5, 6], [6, 5], [6, 6]]
```

Compiled automaton:

```python
Automaton('forest-fire', states=['empty', 'tree', 'burning'], Moore(radius=1, dimensions=2, include_center=False), Fixed(value=0), ConditionalRule)
```

This is a stochastic `ConditionalRule`:

- Burning cells become empty.
- Trees with at least one burning neighbour start burning.
- Trees ignite spontaneously with probability `0.001`.
- Empty cells grow a tree with probability `0.05`.
- Everything else keeps its state through `otherwise: same`, which compiles to
  `Identity()`.

`is_deterministic` is `False`, so stepping needs an RNG:

```python
import numpy as np

grid = spec.build_initial_state()
next_grid = automaton.step(grid, np.random.default_rng(42))
```

The fixed boundary uses `Fixed(0)` because `empty` is the first cell type.

### Totalistic Life

`examples/life-totalistic.cell.yaml`:

```yaml
schema_version: 1
name: life-totalistic

world:
  size: [8, 8]
  boundary: periodic

neighborhood:
  type: moore
  radius: 1

cell_types:
  dead:
    symbol: "."
    color: "#ffffff"
  alive:
    symbol: "#"
    color: "#111111"

rule:
  type: totalistic
  counted: alive
  transitions:
    dead: {3: alive}
    alive: {2: alive, 3: alive}
  default: dead

initial_state:
  fill: dead
  cells:
    alive: [[3, 3], [3, 4], [3, 5]]
```

Compiled automaton:

```python
Automaton('life-totalistic', states=['dead', 'alive'], Moore(radius=1, dimensions=2, include_center=False), Periodic(), TotalisticRule)
```

The rule itself is:

```python
TotalisticRule(counted=1, transitions={0: {3: 1}, 1: {2: 1, 3: 1}}, default=0)
```

This produces the same behaviour as `domain.presets.game_of_life()`, but it is
expressed with a `TotalisticRule` instead of an ordered branch list. The initial grid
is the same 8 by 8 blinker.

### Rule 110

`examples/rule-110.cell.yaml`:

```yaml
schema_version: 1
name: rule-110

world:
  size: [20]
  boundary: {type: fixed, value: dead}

neighborhood:
  type: line
  radius: 1

cell_types:
  dead:
    symbol: "."
    color: "#ffffff"
  alive:
    symbol: "#"
    color: "#111111"

rule:
  type: lookup
  table:
    - {pattern: [dead, dead, dead], then: dead}
    - {pattern: [dead, dead, alive], then: alive}
    - {pattern: [dead, alive, dead], then: alive}
    - {pattern: [dead, alive, alive], then: alive}
    - {pattern: [alive, dead, dead], then: dead}
    - {pattern: [alive, dead, alive], then: alive}
    - {pattern: [alive, alive, dead], then: alive}
    - {pattern: [alive, alive, alive], then: dead}
  default: dead

initial_state:
  fill: dead
  cells:
    alive: [[10]]
```

Compiled automaton:

```python
Automaton('rule-110', states=['dead', 'alive'], Line(radius=1, include_center=True), Fixed(value=0), LookupTableRule)
```

The rule itself is:

```python
LookupTableRule(
    table={
        (0, 0, 0): 0,
        (0, 0, 1): 1,
        (0, 1, 0): 1,
        (0, 1, 1): 1,
        (1, 0, 0): 0,
        (1, 0, 1): 1,
        (1, 1, 0): 1,
        (1, 1, 1): 0,
    },
    default=0,
)
```

This is a one-dimensional `LookupTableRule`. `Line(1)` includes the centre, so each
pattern is `[left, centre, right]`. The initial grid has one live cell at index 10,
and the fixed boundary is `Fixed(0)` because `dead` is index `0`.

## Validation summary

Structural validation (Pydantic) rejects:

- missing or unknown keys;
- wrong scalar types;
- `schema_version` other than `1`;
- `world.size` that is not 1D or 2D;
- invalid `symbol` or `color`;
- malformed neighbourhood, rule, or initial-state objects.

Semantic validation (the compiler) rejects:

- neither or both rule forms;
- `otherwise` not present, duplicated, or not last;
- unknown cell types in conditions, targets, transitions, patterns, `counted`,
  `default`, `fill`, or `cells`;
- counts that are negative or impossible for the neighbourhood;
- random probabilities that are not in `[0, 1]` or do not sum to `1`;
- duplicate lookup patterns or an incomplete lookup table without a default;
- lookup patterns of the wrong length;
- a neighbourhood whose dimensions do not match the world;
- `line` used outside a 1D world;
- initial cells that are out of bounds or assigned more than once.

## Tests

From `cellab/`:

```bash
python -m pytest
```

The parser tests are in `cellab/tests/parser/`. They cover parsing, error paths,
compilation to the domain classes, initial-state construction, deterministic and
stochastic execution, and equivalence with the existing domain presets.

See the root `README.md` for the project overview and
`cellab/domain/README_Domain.md` for the domain model itself.
