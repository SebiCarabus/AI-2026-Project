import numpy as np
import pytest
from hypothesis import given, settings, strategies as st

from domain import Automaton, CellType, Custom, Fixed, Grid, Line, Moore, Periodic, Reflect, VonNeumann
from domain.presets import cyclic, elementary, forest_fire, game_of_life, rule_30
from domain.rules import (
    Branch, CellIs, ConditionalRule, CountInRange, Identity, LookupTableRule, RandomChoice, SetTo,
    TotalisticRule, WithProbability,
)


# --- slow reference implementations, written independently of the domain code ----
def ref_elementary(row, number):
    n = len(row)
    get = lambda i: row[i] if 0 <= i < n else 0  # dead outside
    return [(number >> (get(i - 1) * 4 + get(i) * 2 + get(i + 1))) & 1 for i in range(n)]


def ref_cyclic(grid, k, threshold, offsets):
    h, w = len(grid), len(grid[0])
    out = [row[:] for row in grid]
    for y in range(h):
        for x in range(w):
            nxt = (grid[y][x] + 1) % k
            hits = sum(grid[(y + dy) % h][(x + dx) % w] == nxt for dy, dx in offsets)
            if hits >= threshold:
                out[y][x] = nxt
    return out


# --- Grid ---------------------------------------------------------------------
def test_grid_is_immutable_and_copies_input():
    data = np.array([0, 1, 0])
    g = Grid(data)
    data[0] = 1
    assert g.cells[0] == 0
    with pytest.raises(ValueError):
        g.cells[0] = 1


@given(st.lists(st.lists(st.integers(0, 3), min_size=3, max_size=3), min_size=1, max_size=5))
def test_equal_grids_hash_equally(rows):
    a, b = Grid(rows), Grid(np.array(rows, dtype=np.int64))
    assert a == b and a.hash() == b.hash() and hash(a) == hash(b)


def test_shape_is_part_of_the_hash():
    assert Grid([[0, 0, 0, 0]]).hash() != Grid([[0, 0], [0, 0]]).hash()


def test_grid_rejects_bad_values():
    with pytest.raises(ValueError):
        Grid([0, -1])
    with pytest.raises(TypeError):
        Grid([0.5, 1.0])


def test_text_roundtrip():
    text = "0011\n0221\n3321"
    assert Grid.from_text(text).to_text() == text


# --- Neighbourhoods -----------------------------------------------------------
def test_neighbourhood_sizes():
    assert Moore(1).size == 8
    assert VonNeumann(1).size == 4
    assert Moore(1, include_center=True).size == 9
    assert Line(1).offsets() == ((-1,), (0,), (1,))
    assert Moore(2).reach == 2


def test_custom_rejects_mixed_dimensions():
    with pytest.raises(ValueError):
        Custom(((0, 1), (1,)))


# --- Rule 30 ------------------------------------------------------------------
def test_rule_30_from_single_seed():
    a = rule_30()
    g = Grid.from_text("0000001000000")
    seen = []
    for _ in range(4):
        seen.append(g.to_text())
        g = a.step(g)
    assert seen == [
        "0000001000000",
        "0000011100000",
        "0000110010000",
        "0001101111000",
    ]


def test_wolfram_table_matches_textbook_rule_30():
    t = LookupTableRule.from_wolfram(30).table
    assert t == {(1, 1, 1): 0, (1, 1, 0): 0, (1, 0, 1): 0, (1, 0, 0): 1,
                 (0, 1, 1): 1, (0, 1, 0): 1, (0, 0, 1): 1, (0, 0, 0): 0}


@settings(max_examples=200)
@given(st.integers(0, 255), st.lists(st.integers(0, 1), min_size=1, max_size=40))
def test_every_elementary_rule_matches_reference(number, row):
    assert list(elementary(number).step(Grid(row)).cells) == ref_elementary(row, number)


# --- Cyclic -------------------------------------------------------------------
def test_cyclic_example_reaches_fixed_point():
    # Checked by hand (von Neumann, wrap-around). E.g. step 1:
    #   (0,2)=1 sees up 2, down 2, left 0, right 1 -> two 2s -> becomes 2
    #   (2,0)=3 sees up 0, down 0 (wraps), left 1, right 3 -> two 0s -> becomes 0
    a = cyclic()
    g0 = Grid.from_text("0011\n0221\n3321")
    g1 = a.step(g0)
    g2 = a.step(g1)
    assert g1.to_text() == "0021\n0221\n0321"
    assert g2.to_text() == "0021\n0221\n0021"
    assert a.step(g2) == g2  # stable from generation 2


@settings(max_examples=100)
@given(st.integers(2, 6), st.integers(1, 4), st.integers(2, 8), st.integers(2, 8), st.integers(0, 2**32 - 1))
def test_cyclic_matches_reference(k, threshold, h, w, seed):
    grid = np.random.default_rng(seed).integers(0, k, size=(h, w)).tolist()
    offsets = [(-1, 0), (1, 0), (0, -1), (0, 1)]
    got = cyclic(k, threshold).step(Grid(grid)).cells.tolist()
    assert got == ref_cyclic(grid, k, threshold, offsets)


# --- Game of Life: two rule families must agree ------------------------------
def life_as_conditional():
    return ConditionalRule(
        [
            Branch(CellIs(1) & CountInRange(1, 2, 3), SetTo(1)),
            Branch(CellIs(0) & CountInRange.exactly(1, 3), SetTo(1)),
        ],
        otherwise=SetTo(0),
    )


def test_blinker_oscillates():
    a = game_of_life()
    g = Grid.from_text("00000\n00000\n01110\n00000\n00000")
    g1 = a.step(g)
    assert g1.to_text() == "00000\n00100\n00100\n00100\n00000"
    assert a.step(g1) == g


@settings(max_examples=50)
@given(st.integers(0, 2**32 - 1))
def test_totalistic_and_conditional_life_agree(seed):
    cells = (CellType("dead"), CellType("alive"))
    cond = Automaton("life-c", cells, Moore(1), Periodic(), life_as_conditional())
    g = Grid(np.random.default_rng(seed).integers(0, 2, size=(10, 12)))
    assert game_of_life().step(g) == cond.step(g)


# --- Boundaries ---------------------------------------------------------------
def test_boundaries_differ_at_edges():
    g = Grid([1, 0, 0, 0])
    # Rule 2: new cell is alive iff only the right neighbour was alive.
    assert elementary(2, Fixed(0)).step(g).to_text() == "0000"
    assert elementary(2, Periodic()).step(g).to_text() == "0001"
    assert elementary(2, Reflect()).step(g).to_text() == "0000"


# --- Validation ---------------------------------------------------------------
def test_validation_errors():
    two = (CellType("a"), CellType("b"))
    with pytest.raises(ValueError, match="incomplete"):
        Automaton("x", two, Line(1), Fixed(0), LookupTableRule({(0, 0, 0): 1}))
    with pytest.raises(ValueError, match="length"):
        Automaton("x", two, Moore(1), Fixed(0), LookupTableRule.from_wolfram(30))
    with pytest.raises(ValueError, match="not a valid state"):
        Automaton("x", two, Moore(1), Fixed(0), ConditionalRule([], otherwise=SetTo(5)))
    with pytest.raises(ValueError, match="never hold"):
        Automaton("x", two, VonNeumann(1), Fixed(0),
                  ConditionalRule([Branch(CountInRange.at_least(1, 5), SetTo(1))], otherwise=Identity()))
    with pytest.raises(ValueError, match="impossible"):
        Automaton("x", two, VonNeumann(1), Fixed(0), TotalisticRule(1, {0: {6: 1}}))
    with pytest.raises(ValueError, match="unique"):
        Automaton("x", (CellType("a"), CellType("a")), Line(1), Fixed(0), LookupTableRule.from_wolfram(30))


def test_step_rejects_wrong_grid():
    with pytest.raises(ValueError, match="2D"):
        rule_30().step(Grid([[0, 1], [1, 0]]))
    with pytest.raises(ValueError, match="states"):
        rule_30().step(Grid([0, 2, 0]))

# --- Randomness ---------------------------------------------------------------
EMPTY, TREE, BURNING = 0, 1, 2
THREE = (CellType("a"), CellType("b"), CellType("c"))


def run(automaton, grid, steps, seed):
    rng = np.random.default_rng(seed)  # one generator for the whole run
    for _ in range(steps):
        grid = automaton.step(grid, rng)
    return grid


def test_deterministic_automata_ignore_rng():
    g = Grid.from_text("0000001000000")
    assert rule_30().step(g) == rule_30().step(g, np.random.default_rng(1))
    assert rule_30().is_deterministic and cyclic().is_deterministic and game_of_life().is_deterministic


def test_stochastic_automaton_requires_rng():
    a = forest_fire()
    assert not a.is_deterministic
    with pytest.raises(ValueError, match="rng"):
        a.step(Grid(np.zeros((5, 5), dtype=int)))


def test_same_seed_same_run_different_seed_different_run():
    a = forest_fire(growth=0.3, lightning=0.01)
    g = Grid(np.zeros((30, 30), dtype=int))
    assert run(a, g, 20, seed=42) == run(a, g, 20, seed=42)
    assert run(a, g, 20, seed=42) != run(a, g, 20, seed=43)


def test_forest_fire_deterministic_parts():
    # With no growth and no lightning, only the fire spread is left.
    a = forest_fire(growth=0.0, lightning=0.0)
    g = Grid.from_text("11111\n11211\n11111\n00000\n11111")
    g1 = a.step(g, np.random.default_rng(0))
    assert g1.to_text() == "12221\n12021\n12221\n00000\n11111"


def test_probability_extremes_are_deterministic():
    assert WithProbability(0.0).is_deterministic and WithProbability(1.0).is_deterministic
    assert not WithProbability(0.5).is_deterministic
    assert RandomChoice({1: 1.0}).is_deterministic
    assert not RandomChoice({0: 0.5, 1: 0.5}).is_deterministic


def test_with_probability_frequency():
    rule = ConditionalRule([Branch(WithProbability(0.3), SetTo(1))], otherwise=SetTo(0))
    a = Automaton("p", THREE, Moore(1), Fixed(0), rule)
    g = a.step(Grid(np.zeros((200, 200), dtype=int)), np.random.default_rng(7))
    assert abs(g.count(1) / g.cells.size - 0.3) < 0.01


def test_random_choice_frequency():
    rule = ConditionalRule([], otherwise=RandomChoice({0: 0.5, 1: 0.3, 2: 0.2}))
    a = Automaton("c", THREE, Moore(1), Fixed(0), rule)
    g = a.step(Grid(np.zeros((200, 200), dtype=int)), np.random.default_rng(7))
    freq = g.counts(3) / g.cells.size
    assert np.allclose(freq, [0.5, 0.3, 0.2], atol=0.01)


def test_random_validation():
    with pytest.raises(ValueError, match="not in"):
        Automaton("x", THREE, Moore(1), Fixed(0),
                  ConditionalRule([Branch(WithProbability(1.5), SetTo(1))], otherwise=Identity()))
    with pytest.raises(ValueError, match="sum"):
        Automaton("x", THREE, Moore(1), Fixed(0), ConditionalRule([], otherwise=RandomChoice({0: 0.5, 1: 0.2})))