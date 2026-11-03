import numpy as np
import pytest

from domain import (
    ArrayUniverse, Automaton, CellType, Fixed, Grid, Line, Periodic, Reflect, TreeUniverse,
)
from domain.presets import elementary, game_of_life
from domain.rules import ConditionalRule, Identity


def sparse_from_grid(grid):
    return {
        tuple(int(c) for c in coord): int(grid.cells[tuple(coord)])
        for coord in np.argwhere(grid.cells != 0)
    }


@pytest.mark.parametrize("boundary", [Periodic(), Fixed(0)])
def test_tree_life_matches_array_generations(boundary):
    automaton = game_of_life(boundary)
    grid = Grid.from_text("000000\n001000\n000100\n011100\n000000")
    tree = TreeUniverse.from_sparse(automaton, sparse_from_grid(grid), bounds=grid.shape)
    array = ArrayUniverse(automaton, grid)

    for generation in range(7):
        assert tree.window((0, 0), grid.shape).tolist() == array.grid.cells.tolist()
        assert tree.counts() == array.counts()
        assert tree.generation == array.generation == generation
        tree = tree.step()
        array = array.step()


def test_tree_rule_30_matches_array_with_finite_fixed_edges():
    automaton = elementary(30, Fixed(0))
    grid = Grid([0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0])
    tree = TreeUniverse.from_sparse(automaton, sparse_from_grid(grid), bounds=grid.shape)
    array = ArrayUniverse(automaton, grid)

    for _ in range(12):
        assert tree.window((0,), grid.shape).tolist() == array.grid.cells.tolist()
        tree, array = tree.step(), array.step()


def test_infinite_rule_30_grows_and_matches_a_large_fixed_viewport():
    automaton = elementary(30, Fixed(0))
    tree = TreeUniverse.from_sparse(automaton, {(0,): 1})
    viewport_cells = np.zeros(81, dtype=np.uint8)
    viewport_cells[40] = 1
    array = ArrayUniverse(automaton, Grid(viewport_cells))

    for generation in range(15):
        assert tree.window((-generation - 2,), (generation * 2 + 5,)).shape == (generation * 2 + 5,)
        assert tree.window((-40,), (81,)).tolist() == array.grid.cells.tolist()
        tree, array = tree.step(), array.step()
    assert len(tree.export_sparse()) > 1
    assert tree.bounds is None
    assert 0 not in tree.counts()


def test_infinite_life_2d_matches_a_fixed_viewport():
    automaton = game_of_life(Fixed(0))
    pattern = Grid.from_text("010\n001\n111")
    initial = {(row - 1, col - 1): int(pattern.cells[row, col])
               for row, col in np.argwhere(pattern.cells != 0)}
    tree = TreeUniverse.from_sparse(automaton, initial)
    viewport = np.zeros((30, 30), dtype=np.uint8)
    for (row, col), state in initial.items():
        viewport[row + 15, col + 15] = state
    array = ArrayUniverse(automaton, Grid(viewport))

    for _ in range(8):
        assert tree.window((-15, -15), (30, 30)).tolist() == array.grid.cells.tolist()
        tree, array = tree.step(), array.step()
    assert tree.bounds is None


def test_tree_counts_windows_fingerprint_and_export():
    automaton = game_of_life(Fixed(0))
    tree = TreeUniverse.from_sparse(automaton, {(-1, 0): 1})
    assert list(tree.iter_nonzero()) == [((-1, 0), 1)]
    assert tree.counts() == {1: 1}
    assert tree.window((-2, -1), (3, 3)).tolist() == [[0, 0, 0], [0, 1, 0], [0, 0, 0]]
    assert tree.fingerprint() == TreeUniverse.from_sparse(automaton, {(-1, 0): 1}).fingerprint()
    exported = tree.export_sparse()
    exported[(4, 4)] = 1
    assert len(list(tree.iter_nonzero())) == 1

    finite = TreeUniverse.from_sparse(automaton, {(0, 0): 1}, bounds=(4, 5))
    assert finite.counts() == {0: 19, 1: 1}
    assert finite.bounds == (4, 5)
    with pytest.raises(ValueError, match="outside"):
        finite.window((-1, 0), (1, 1))


def test_nodes_are_interned_and_shared_across_generations():
    automaton = elementary(204, Fixed(0))  # identity on all binary neighbourhoods
    first = TreeUniverse.from_sparse(automaton, {(0,): 1, (2,): 1})
    same = TreeUniverse.from_sparse(automaton, {(0,): 1, (2,): 1})
    assert first.root is same.root
    assert first.root.children[0] is first.root.children[1]
    assert first.step().root is first.root


def test_tree_rejects_unsupported_automata_and_boundaries():
    from domain.presets import forest_fire

    with pytest.raises(ValueError, match="deterministic"):
        TreeUniverse.from_sparse(forest_fire(), {})
    with pytest.raises(ValueError, match="quiescent"):
        TreeUniverse.from_sparse(elementary(1), {})

    reach_two = Automaton(
        "wide", (CellType("dead"), CellType("alive")), Line(2), Fixed(0),
        ConditionalRule((), otherwise=Identity()),
    )
    with pytest.raises(ValueError, match="reach at most 1"):
        TreeUniverse.from_sparse(reach_two, {})

    reflected = Automaton(
        "reflected", (CellType("dead"), CellType("alive")), Line(1), Reflect(),
        ConditionalRule((), otherwise=Identity()),
    )
    with pytest.raises(ValueError, match=r"Periodic and Fixed\(0\)"):
        TreeUniverse.from_sparse(reflected, {}, bounds=(8,))

    nonzero_fixed = Automaton(
        "nonzero-edge", (CellType("dead"), CellType("alive")), Line(1), Fixed(1),
        ConditionalRule((), otherwise=Identity()),
    )
    with pytest.raises(ValueError, match=r"Fixed\(0\)"):
        TreeUniverse.from_sparse(nonzero_fixed, {}, bounds=(8,))
