from pathlib import Path

import numpy as np
import pytest

from domain import (
    Automaton,
    Custom,
    Fixed,
    Grid,
    Line,
    Moore,
    Periodic,
    Reflect,
    VonNeumann,
)
from domain.presets import elementary, game_of_life
from domain.rules import ConditionalRule, LookupTableRule, TotalisticRule
from parser import load_spec, parse_spec

EXAMPLES = Path(__file__).parents[2] / "parser" / "examples"


def run(automaton, grid: Grid, steps: int, seed: int) -> Grid:
    rng = np.random.default_rng(seed)
    for _ in range(steps):
        grid = automaton.step(grid, rng)
    return grid


def test_life_compiles_to_expected_domain_objects():
    spec = load_spec(EXAMPLES / "conway-life.cell.yaml")
    automaton = spec.compile()

    assert isinstance(automaton, Automaton)
    assert automaton.name == "conway-life"
    assert automaton.index_of("dead") == 0
    assert automaton.index_of("alive") == 1
    assert isinstance(automaton.neighborhood, Moore)
    assert isinstance(automaton.boundary, Periodic)
    assert isinstance(automaton.rule, ConditionalRule)
    assert automaton.is_deterministic


def test_life_blinker_oscillates():
    automaton = load_spec(EXAMPLES / "conway-life.cell.yaml").compile()
    grid = Grid.from_text("00000\n00000\n01110\n00000\n00000")

    next_grid = automaton.step(grid)
    assert next_grid.to_text() == "00000\n00100\n00100\n00100\n00000"
    assert automaton.step(next_grid) == grid


def test_totalistic_matches_game_of_life_preset():
    automaton = load_spec(EXAMPLES / "life-totalistic.cell.yaml").compile()
    assert isinstance(automaton.rule, TotalisticRule)

    grid = Grid(np.random.default_rng(0).integers(0, 2, size=(10, 12)))
    assert automaton.step(grid) == game_of_life().step(grid)


def test_lookup_matches_elementary_rule_110():
    automaton = load_spec(EXAMPLES / "rule-110.cell.yaml").compile()
    assert isinstance(automaton.rule, LookupTableRule)

    grid = Grid(np.random.default_rng(0).integers(0, 2, size=20))
    assert automaton.step(grid) == elementary(110, Fixed(0)).step(grid)


def test_probabilistic_automaton_is_stochastic_and_reproducible():
    spec = load_spec(EXAMPLES / "forest-fire.cell.yaml")
    automaton = spec.compile()
    grid = spec.build_initial_state()
    assert grid is not None

    assert isinstance(automaton.rule, ConditionalRule)
    assert not automaton.is_deterministic
    with pytest.raises(ValueError, match="rng"):
        automaton.step(grid)

    assert run(automaton, grid, 5, 42) == run(automaton, grid, 5, 42)


def test_initial_state_uses_row_column_coordinates():
    text = """
schema_version: 1
name: coordinates
world: {size: [4, 2], boundary: reflect}
neighborhood: {type: moore, radius: 1}
cell_types:
  dead: {symbol: ".", color: "#ffffff"}
  alive: {symbol: "#", color: "#111111"}
rules:
  - otherwise: dead
initial_state:
  fill: dead
  cells:
    alive: [[0, 3], [1, 0]]
"""
    spec = parse_spec(text)
    assert spec.world.width == 4
    assert spec.world.height == 2
    assert spec.world.grid_shape == (2, 4)

    grid = spec.build_initial_state()
    assert grid is not None
    assert grid.shape == (2, 4)
    assert grid.to_text() == "0001\n1000"


def test_initial_state_is_none_when_omitted():
    text = (EXAMPLES / "conway-life.cell.yaml").read_text(encoding="utf-8")
    spec = parse_spec(text.split("initial_state:")[0])
    assert spec.initial_state is None
    assert spec.build_initial_state() is None


def test_boundary_variants_compile():
    base = (EXAMPLES / "conway-life.cell.yaml").read_text(encoding="utf-8")

    periodic = parse_spec(base.replace("boundary: torus", "boundary: periodic"))
    assert isinstance(periodic.compile().boundary, Periodic)

    reflect = parse_spec(base.replace("boundary: torus", "boundary: reflect"))
    assert isinstance(reflect.compile().boundary, Reflect)

    fixed = parse_spec(
        base.replace("boundary: torus", "boundary: {type: fixed, value: dead}")
    )
    assert fixed.compile().boundary == Fixed(0)


def test_neighbourhood_variants_compile():
    base = (EXAMPLES / "conway-life.cell.yaml").read_text(encoding="utf-8")

    von_neumann = parse_spec(
        base.replace("type: moore", "type: von_neumann")
    ).compile()
    assert isinstance(von_neumann.neighborhood, VonNeumann)
    assert von_neumann.neighborhood.size == 4

    custom = parse_spec(
        base.replace(
            "  type: moore\n  radius: 1",
            "  type: custom\n  points: [[-1, 0], [1, 0], [0, -1], [0, 1]]",
        )
    ).compile()
    assert isinstance(custom.neighborhood, Custom)
    assert custom.neighborhood.offsets() == ((-1, 0), (1, 0), (0, -1), (0, 1))


def test_one_dimensional_moore_is_inferred():
    text = """
schema_version: 1
name: one-dimensional
world: {size: [10], boundary: periodic}
neighborhood: {type: moore, radius: 1}
cell_types:
  dead: {symbol: ".", color: "#ffffff"}
  alive: {symbol: "#", color: "#111111"}
rules:
  - otherwise: dead
"""
    automaton = parse_spec(text).compile()
    assert isinstance(automaton.neighborhood, Moore)
    assert automaton.neighborhood.ndim == 1
    assert automaton.neighborhood.size == 2


def test_examples_compile_and_build_initial_states():
    for path in sorted(EXAMPLES.glob("*.cell.yaml")):
        spec = load_spec(path)
        automaton = spec.compile()
        grid = spec.build_initial_state()

        assert automaton.name == spec.name
        assert grid is not None
        assert grid.shape == spec.world.grid_shape
