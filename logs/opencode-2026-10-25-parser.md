# CelLab parser planning and implementation log

Date: 2026-10-25

## Goal

Read the custom CelLab YAML format (`*.cell.yaml`) and compile it into the existing
`cellab/domain` `Automaton` objects, with strict validation and actionable error paths.

## Agreed API

- `parse_spec(text) -> AutomatonSpec`
- `load_spec(path) -> AutomatonSpec`
- `AutomatonSpec.compile() -> Automaton`
- `AutomatonSpec.build_initial_state() -> Grid | None`
- `SpecError(ValueError)` with `.issues: list[SpecIssue]`, where each issue has
  `path` and `message`.

Implementation uses Pydantic v2 for the typed intermediate representation and PyYAML's
safe loader for parsing.

## Grammar decisions

- A document describes exactly one automaton.
- Exactly one of `rules` (ordered conditional/probabilistic branches) or `rule`
  (totalistic/lookup) must be present.
- Conditional rules use first-match-wins and require exactly one final `otherwise`.
  `when` supports `cell`, `count`, and `probability`; `count` accepts an integer or
  `[min, max]` with nullable bounds. Targets are a cell type, `same`, or
  `{random: {name: probability}}`.
- Totalistic rules use `counted`, `transitions`, and an optional `default`.
  Omitting `default` means the cell stays unchanged.
- Lookup rules use `table` entries of `{pattern, then}` read in neighbourhood offset
  order, plus an optional `default`. If `default` is omitted the table must be complete.
- `world.size` is `[width, height]` for 2D and `[length]` for 1D. Initial-state
  coordinates are `[row, col]` for 2D and `[index]` for 1D.
- Boundaries are `torus`, `periodic`, `reflect`, or `{type: fixed, value: <cell>}`.
- Neighbourhoods are `moore`, `von_neumann`, `line`, or `custom`. `radius` defaults to
  `1`. Dimensions are inferred from the world. `line` is 1D only. `custom` uses `points`.
- Cell metadata requires a unique one-character non-whitespace `symbol` and a
  `#RRGGBB` `color`.
- `initial_state` is optional. `fill` is optional and defaults to the first cell type.
  `cells` maps cell type names to coordinate lists.
- Unknown keys are rejected. Duplicate YAML keys are detected before Pydantic
  validation. Error paths are cleaned to hide Pydantic union internals.

## Deliberately out of scope

`cell: [names]`, `not`, `include_center`, 3D worlds, overlap/reachability warnings,
spec hashing, and natural-language parsing.

## Files added

- `cellab/parser/errors.py` - `SpecIssue`, `SpecError`
- `cellab/parser/models.py` - Pydantic IR and `compile()` / `build_initial_state()`
- `cellab/parser/compiler.py` - semantic validation and domain compilation
- `cellab/parser/parser.py` - YAML loading, duplicate-key detection, error formatting
- `cellab/parser/__init__.py` - public exports
- `cellab/parser/examples/conway-life.cell.yaml`
- `cellab/parser/examples/forest-fire.cell.yaml`
- `cellab/parser/examples/life-totalistic.cell.yaml`
- `cellab/parser/examples/rule-110.cell.yaml`
- `cellab/tests/parser/test_parser.py`
- `cellab/tests/parser/test_compiler.py`
- `requirements.txt`
- Updated the root `README.md` spec-format section with the full grammar.

## Verification

- `cd cellab && python -m pytest -q` -> `69 passed`
- The four examples compile successfully.
- Conway Life compiles to a deterministic `ConditionalRule` with a `Moore` neighbourhood
  and `Periodic` boundary.
- Forest fire compiles to a stochastic automaton; stepping requires an RNG and the same
  seed reproduces the same result.
- The totalistic example matches the existing game-of-life preset.
- The lookup example matches `elementary(110, Fixed(0))`.
- Initial-state `[row, col]` handling is verified with a non-square world.
- Error cases cover unknown keys, duplicate YAML keys, invalid cell metadata, missing
  `otherwise`, invalid counts and probabilities, boundary/neighbourhood mismatches,
  out-of-bounds initial cells, and invalid lookup/totalistic tables.

## Notes

- Tests are run from `cellab` so the existing top-level `domain` and `parser` imports
  work. No `__init__.py` is added under `cellab/tests/parser/`.
- `hypothesis` was missing from the environment and was installed so the existing
  domain property tests could run; it is now declared in `requirements.txt`.
