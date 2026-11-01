import numpy as np
import pytest
from hypothesis import given, settings, strategies as st

from domain import (
    COMPASS, ArrayUniverse, Automaton, CellType, Custom, Diagonal, Fixed, Grid, Line, Moore, Periodic,
    Reflect, VonNeumann,
)
from domain.presets import cyclic, elementary, forest_fire, game_of_life, rule_30
from domain.rules import (
    And, Branch, CellIn, CellIs, ConditionalRule, CountInRange, CountInSet, Identity, LookupTableRule,
    MoreThan, NeighbourIn, Not, Or, RandomChoice, SetTo, TotalisticRule, WithProbability,
)

TWO = (CellType("dead"), CellType("alive"))
THREE = (CellType("a"), CellType("b"), CellType("c"))


def automaton(rule, cells=TWO, neighbourhood=None, boundary=None):
    return Automaton("t", cells, neighbourhood or Moore(1), boundary or Periodic(), rule)


def random_grid(seed, k, shape):
    return Grid(np.random.default_rng(seed).integers(0, k, size=shape))


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


def ref_brians_brain(grid):
    h, w = len(grid), len(grid[0])
    out = [row[:] for row in grid]
    for y in range(h):
        for x in range(w):
            firing = sum(grid[(y + dy) % h][(x + dx) % w] == 1
                         for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx)
            out[y][x] = {1: 2, 2: 0}.get(grid[y][x], 1 if firing == 2 else 0)
    return out


def ref_voter(grid):
    h, w = len(grid), len(grid[0])
    out = [row[:] for row in grid]
    for y in range(h):
        for x in range(w):
            b = sum(grid[(y + dy) % h][(x + dx) % w] for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx)
            a = 8 - b
            if grid[y][x] == 0 and b > a:
                out[y][x] = 1
            elif grid[y][x] == 1 and a > b:
                out[y][x] = 0
    return out


# Langton's Ant (spec 8.5). States: white, black, then white_ant_{n,e,s,w}, black_ant_{n,e,s,w}.
WHITE, BLACK = 0, 1
HEADINGS = "nesw"
STEP = {"n": (-1, 0), "e": (0, 1), "s": (1, 0), "w": (0, -1)}


def ant(colour, heading):
    return 2 + 4 * colour + HEADINGS.index(heading)


def ref_langton(h, w, steps):
    """Plain agent simulation: on white turn right, on black turn left; flip; move."""
    colours = [[WHITE] * w for _ in range(h)]
    y, x, heading = h // 2, w // 2, "n"
    frames = []
    for _ in range(steps + 1):
        frame = [row[:] for row in colours]
        frame[y][x] = ant(colours[y][x], heading)
        frames.append(frame)
        turn = 1 if colours[y][x] == WHITE else -1
        heading = HEADINGS[(HEADINGS.index(heading) + turn) % 4]
        colours[y][x] = 1 - colours[y][x]
        dy, dx = STEP[heading]
        y, x = (y + dy) % h, (x + dx) % w
    return frames


# --- Grid ---------------------------------------------------------------------
def test_grid_is_immutable_and_copies_input():
    data = np.array([0, 1, 0])
    g = Grid(data)
    data[0] = 1
    assert g.cells[0] == 0
    with pytest.raises(ValueError):
        g.cells[0] = 1


def test_uint8_input_is_still_copied():
    data = np.zeros(3, dtype=np.uint8)
    g = Grid(data)
    data[0] = 1
    assert g.cells[0] == 0


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


def test_step_output_is_an_ordinary_immutable_grid():
    g = game_of_life().step(Grid.from_text("00000\n00000\n01110\n00000\n00000"))
    expected = Grid.from_text("00000\n00100\n00100\n00100\n00000")
    assert g.cells.dtype == np.uint8 and not g.cells.flags.writeable
    assert g == expected and g.hash() == expected.hash()


def test_lookup_rule_output_is_converted_to_uint8():
    assert rule_30().step(Grid([0, 1, 0])).cells.dtype == np.uint8


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


def test_diagonal_offsets():
    assert Diagonal(1).offsets() == ((-1, -1), (-1, 1), (1, -1), (1, 1))
    assert Diagonal(2).size == 8
    assert Diagonal(2).reach == 2
    assert (0, 0) not in Diagonal(3).offsets()


@pytest.mark.parametrize("n, size", [(Moore(1), 8), (VonNeumann(1), 4), (Diagonal(1), 4)])
def test_compass_names_point_at_their_offsets(n, size):
    names = n.names()
    assert len(names) == size
    for name, i in names.items():
        assert COMPASS[n.offsets()[i]] == name


def test_von_neumann_names():
    assert set(VonNeumann(1).names()) == {"north", "east", "south", "west"}
    assert VonNeumann(1).offsets()[VonNeumann(1).index_of("west")] == (0, -1)


def test_presets_beyond_radius_1_or_2d_have_no_names():
    assert Moore(2).names() == {}
    assert VonNeumann(1, dimensions=1).names() == {}
    with pytest.raises(KeyError, match="none"):
        Moore(2).index_of("north")


def test_custom_named():
    n = Custom.named({"left": (0, -1), "right": (0, 1)})
    assert n.offsets() == ((0, -1), (0, 1))
    assert n.index_of("right") == 1
    assert Custom(((0, 1),)).names() == {}
    with pytest.raises(KeyError, match="left, right"):
        n.index_of("up")


def test_custom_name_validation():
    with pytest.raises(ValueError, match="unique"):
        Custom(((0, 1), (0, -1)), ("a", "a"))
    with pytest.raises(ValueError, match="2 points"):
        Custom(((0, 1), (0, -1)), ("a",))


# --- Conditions ---------------------------------------------------------------
NEIGHBOURS = np.array([[0, 1, 2, 2], [1, 1, 0, 0], [2, 2, 2, 0]], dtype=np.uint8)
CURRENT = np.array([0, 1, 2], dtype=np.uint8)


def test_cell_in_group():
    assert CellIn({1, 2}).mask(CURRENT, NEIGHBOURS).tolist() == [False, True, True]
    assert CellIn(0).mask(CURRENT, NEIGHBOURS).tolist() == [True, False, False]


def test_counts_over_a_group():
    # neighbours in {1, 2}: 3, 2, 3
    assert CountInRange({1, 2}, 3).mask(CURRENT, NEIGHBOURS).tolist() == [True, False, True]
    assert CountInRange({1, 2}, maximum=2).mask(CURRENT, NEIGHBOURS).tolist() == [False, True, False]
    assert CountInSet({1, 2}, {2}).mask(CURRENT, NEIGHBOURS).tolist() == [False, True, False]
    assert CountInSet(2, [0, 3]).mask(CURRENT, NEIGHBOURS).tolist() == [False, True, True]


def test_more_than_and_ties():
    # counts of 2 vs 1: (2, 1), (0, 2), (3, 0)
    assert MoreThan(2, 1).mask(CURRENT, NEIGHBOURS).tolist() == [True, False, True]
    tie = np.array([[1, 2]], dtype=np.uint8)
    assert not MoreThan(1, 2).mask(np.zeros(1, np.uint8), tie)[0]
    assert not MoreThan(2, 1).mask(np.zeros(1, np.uint8), tie)[0]


def test_neighbour_in():
    assert NeighbourIn(3, {0, 2}).mask(CURRENT, NEIGHBOURS).tolist() == [True, True, True]
    assert NeighbourIn(1, 1).mask(CURRENT, NEIGHBOURS).tolist() == [True, True, False]


def test_counting_is_per_offset_on_tiny_wrapped_worlds():
    # On a 1x1 torus all 8 Moore offsets land on the cell itself.
    a = automaton(ConditionalRule([Branch(CountInSet(1, {8}), SetTo(0))], otherwise=Identity()))
    assert a.step(Grid([[1]])).to_text() == "0"


def test_conditions_are_hashable_and_compare_by_value():
    assert CellIn([1, 2]) == CellIn({2, 1})
    assert len({CountInRange(1, 2, 3), CountInRange({1}, 2, 3), MoreThan(0, 1)}) == 2


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


def elementary_v1(number):
    """Any elementary rule as spec-style positional branches on a height-1 world."""
    n = Custom.named({"left": (0, -1), "right": (0, 1)})
    left, right = n.index_of("left"), n.index_of("right")
    branches = []
    for centre in (0, 1):
        for l in (0, 1):
            for r in (0, 1):
                result = (number >> (l * 4 + centre * 2 + r)) & 1
                if result != centre:
                    cond = CellIn(centre) & NeighbourIn(left, l) & NeighbourIn(right, r)
                    branches.append(Branch(cond, SetTo(result)))
    return n, ConditionalRule(branches, otherwise=Identity())


@settings(max_examples=300)
@given(st.integers(0, 255), st.lists(st.integers(0, 1), min_size=1, max_size=30), st.booleans())
def test_every_elementary_rule_as_positional_branches(number, row, wrap):
    n, rule = elementary_v1(number)
    boundary = Periodic() if wrap else Fixed(0)
    a = Automaton(f"rule-{number}", TWO, n, boundary, rule)
    got = a.step(Grid([row])).cells[0].tolist()
    assert got == elementary(number, boundary).step(Grid(row)).cells.tolist()


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


# --- Game of Life: three rule families must agree ----------------------------
def life_as_conditional():
    return ConditionalRule(
        [
            Branch(CellIs(1) & CountInRange(1, 2, 3), SetTo(1)),
            Branch(CellIs(0) & CountInRange.exactly(1, 3), SetTo(1)),
        ],
        otherwise=SetTo(0),
    )


def life_v1():
    """Spec 8.1: dead -> alive on 3; alive -> dead unless 2 or 3."""
    dead, alive = 0, 1
    return ConditionalRule(
        [
            Branch(CellIn(dead) & CountInSet(alive, {3}), SetTo(alive)),
            Branch(CellIn(alive) & Not(CountInSet(alive, {2, 3})), SetTo(dead)),
        ],
        otherwise=Identity(),
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


@settings(max_examples=50)
@given(st.integers(0, 2**32 - 1))
def test_life_v1_matches_preset(seed):
    g = random_grid(seed, 2, (9, 11))
    assert automaton(life_v1()).step(g) == game_of_life().step(g)


# --- Other spec v1 examples ---------------------------------------------------
@settings(max_examples=50)
@given(st.integers(0, 2**32 - 1))
def test_brians_brain_matches_reference(seed):
    off, firing, dying = 0, 1, 2
    rule = ConditionalRule(
        [
            Branch(CellIn(firing), SetTo(dying)),
            Branch(CellIn(dying), SetTo(off)),
            Branch(CellIn(off) & CountInSet(firing, {2}), SetTo(firing)),
        ],
        otherwise=Identity(),
    )
    g = random_grid(seed, 3, (8, 10))
    assert automaton(rule, THREE).step(g).cells.tolist() == ref_brians_brain(g.cells.tolist())


@settings(max_examples=50)
@given(st.integers(0, 2**32 - 1))
def test_voter_matches_reference(seed):
    a_, b_ = 0, 1
    rule = ConditionalRule(
        [
            Branch(CellIn(a_) & MoreThan(b_, a_), SetTo(b_)),
            Branch(CellIn(b_) & MoreThan(a_, b_), SetTo(a_)),
        ],
        otherwise=Identity(),
    )
    g = random_grid(seed, 2, (7, 9))
    assert automaton(rule).step(g).cells.tolist() == ref_voter(g.cells.tolist())


def langtons_ant():
    n = VonNeumann(1)
    on_white = {ant(WHITE, h) for h in HEADINGS}
    on_black = {ant(BLACK, h) for h in HEADINGS}
    # An ant arriving with heading h comes from the opposite side; which ants turn into h.
    arrivals = {
        "e": ("west", {ant(WHITE, "n"), ant(BLACK, "s")}),
        "s": ("north", {ant(WHITE, "e"), ant(BLACK, "w")}),
        "w": ("east", {ant(WHITE, "s"), ant(BLACK, "n")}),
        "n": ("south", {ant(WHITE, "w"), ant(BLACK, "e")}),
    }
    branches = [Branch(CellIn(on_white), SetTo(BLACK)), Branch(CellIn(on_black), SetTo(WHITE))]
    for colour in (WHITE, BLACK):
        for heading in "eswn":
            position, movers = arrivals[heading]
            cond = CellIn(colour) & NeighbourIn(n.index_of(position), movers)
            branches.append(Branch(cond, SetTo(ant(colour, heading))))
    cells = [CellType(f"s{i}") for i in range(10)]
    return Automaton("langton", cells, n, Periodic(), ConditionalRule(branches, otherwise=Identity()))


def test_langtons_ant_matches_agent_simulation():
    frames = ref_langton(24, 24, 600)
    a = langtons_ant()
    g = Grid(frames[0])
    for expected in frames[1:]:
        g = a.step(g)
        assert g.cells.tolist() == expected


# --- Boundaries ---------------------------------------------------------------
def test_boundaries_differ_at_edges():
    g = Grid([1, 0, 0, 0])
    # Rule 2: new cell is alive iff only the right neighbour was alive.
    assert elementary(2, Fixed(0)).step(g).to_text() == "0000"
    assert elementary(2, Periodic()).step(g).to_text() == "0001"
    assert elementary(2, Reflect()).step(g).to_text() == "0000"


def test_fixed_boundary_value_is_checked():
    with pytest.raises(ValueError, match="fixed boundary value 7"):
        elementary(110, boundary=Fixed(7))


# --- Validation ---------------------------------------------------------------
def test_validation_errors():
    two = (CellType("a"), CellType("b"))
    with pytest.raises(ValueError, match="incomplete"):
        Automaton("x", two, Line(1), Fixed(0), LookupTableRule({(0, 0, 0): 1}))
    with pytest.raises(ValueError, match="length"):
        Automaton("x", two, Moore(1), Fixed(0), LookupTableRule.from_wolfram(30))
    with pytest.raises(ValueError, match="not a valid state"):
        Automaton("x", two, Moore(1), Fixed(0), ConditionalRule([], otherwise=SetTo(5)))
    with pytest.raises(ValueError, match="minimum greater than maximum"):
        Automaton("x", two, VonNeumann(1), Fixed(0),
                  ConditionalRule([Branch(CountInRange(1, 3, 2), SetTo(1))], otherwise=Identity()))
    # A range that can never hold (5 of 4 neighbours) is legal; it just never matches.
    Automaton("x", two, VonNeumann(1), Fixed(0),
              ConditionalRule([Branch(CountInRange.at_least(1, 5), SetTo(1))], otherwise=Identity()))
    with pytest.raises(ValueError, match="impossible"):
        Automaton("x", two, VonNeumann(1), Fixed(0), TotalisticRule(1, {0: {6: 1}}))
    with pytest.raises(ValueError, match="unique"):
        Automaton("x", (CellType("a"), CellType("a")), Line(1), Fixed(0), LookupTableRule.from_wolfram(30))


def test_condition_validation():
    def build(cond):
        return automaton(ConditionalRule([Branch(cond, SetTo(1))], otherwise=Identity()))

    with pytest.raises(ValueError, match="at least one part"):
        build(And(()))
    with pytest.raises(ValueError, match="at least one part"):
        build(Or([]))
    with pytest.raises(ValueError, match="outside a neighbourhood"):
        build(NeighbourIn(8, 1))
    with pytest.raises(ValueError, match="not a valid state"):
        build(CellIn({0, 5}))
    with pytest.raises(ValueError, match="at least one state"):
        build(CellIn(()))
    with pytest.raises(ValueError, match="non-negative"):
        build(CountInSet(1, {-1}))
    with pytest.raises(ValueError, match="non-negative"):
        build(CountInRange(1, -1))
    with pytest.raises(ValueError, match="not a valid state"):
        build(MoreThan(1, 3))


def test_step_rejects_wrong_grid():
    with pytest.raises(ValueError, match="2D"):
        rule_30().step(Grid([[0, 1], [1, 0]]))
    with pytest.raises(ValueError, match="states"):
        rule_30().step(Grid([0, 2, 0]))


# --- Quiescence ---------------------------------------------------------------
def test_is_quiescent():
    assert game_of_life().is_quiescent()
    assert rule_30().is_quiescent()
    assert automaton(life_v1()).is_quiescent()
    assert not elementary(1).is_quiescent()  # 000 -> 1
    wakes = ConditionalRule([Branch(CellIn(0) & CountInSet(0, {8}), SetTo(1))], otherwise=Identity())
    assert not automaton(wakes).is_quiescent()
    assert not forest_fire().is_quiescent()  # stochastic


# --- Randomness ---------------------------------------------------------------
EMPTY, TREE, BURNING = 0, 1, 2


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


def test_degenerate_randomness_needs_no_rng():
    rule = ConditionalRule(
        [Branch(WithProbability(0.0), SetTo(2)), Branch(CellIn(0) & WithProbability(1.0), SetTo(1))],
        otherwise=RandomChoice({0: 0.0, 2: 1.0}),
    )
    a = automaton(rule, THREE)
    assert a.is_deterministic
    assert a.step(Grid([[0, 1], [2, 0]])).to_text() == "12\n21"


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


# --- ArrayUniverse ------------------------------------------------------------
def test_array_universe_steps_and_counts():
    a = game_of_life()
    g = Grid.from_text("00000\n00000\n01110\n00000\n00000")
    u = ArrayUniverse(a, g)
    u3 = u.step(3)
    assert u3.generation == 3 and u.generation == 0
    assert u3.grid == a.step(a.step(a.step(g)))
    assert u3.counts() == {0: 22, 1: 3}
    assert u.step(2).fingerprint() == u.fingerprint()  # blinker has period 2
    assert u.step(1).fingerprint() != u.fingerprint()
    assert u.step(0).grid is g
    assert u.bounds == (5, 5)


def test_array_universe_window():
    u = ArrayUniverse(langtons_ant(), Grid.from_text("0123\n4567\n8901"))  # 10 states
    assert u.window([1, 1], [2, 2]).tolist() == [[5, 6], [9, 0]]
    with pytest.raises(ValueError, match="outside"):
        u.window([2, 2], [2, 2])
    with pytest.raises(ValueError, match="2 values"):
        u.window([0], [1])


def test_array_universe_rejects_bad_input():
    with pytest.raises(ValueError, match="states"):
        ArrayUniverse(game_of_life(), Grid([[0, 2]]))
    with pytest.raises(ValueError, match=">= 0"):
        ArrayUniverse(game_of_life(), Grid([[0, 1]])).step(-1)


def test_array_universe_passes_rng_through():
    a = forest_fire(growth=0.5)
    u = ArrayUniverse(a, Grid(np.zeros((10, 10), dtype=int)))
    one = u.step(5, np.random.default_rng(3)).grid
    two = u.step(5, np.random.default_rng(3)).grid
    assert one == two and one.count(1) > 0
