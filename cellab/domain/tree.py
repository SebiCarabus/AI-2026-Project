"""Hash-consed sparse tree universes for deterministic quiescent automata.

The tree stores only non-zero cells. 1-D blocks are binary trees and 2-D blocks
are quadtrees; immutable nodes are interned so equal blocks share identity across
roots and generations. Stepping evaluates only the non-zero support and its
neighbourhood frontier.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from numbers import Integral
from weakref import WeakValueDictionary

import numpy as np

from .automaton import Automaton
from .boundary import Fixed, Periodic
from .grid import DTYPE
from .universe import Universe


class _Node:
    """An immutable canonical node. Leaves have `state`; branches have children."""

    __slots__ = ("level", "state", "children", "counts", "__weakref__")

    def __init__(self, level: int, state: int | None, children: tuple[_Node, ...], counts: tuple[int, ...]):
        object.__setattr__(self, "level", level)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "children", children)
        object.__setattr__(self, "counts", counts)

    def __setattr__(self, name, value):
        raise AttributeError("tree nodes are immutable")


# Live roots retain their nodes; the weak interner canonicalizes nodes shared by
# those roots without keeping discarded generations alive indefinitely.
_INTERN: WeakValueDictionary[tuple, _Node] = WeakValueDictionary()


def _leaf(state: int, num_states: int) -> _Node:
    key = (0, state, num_states)
    node = _INTERN.get(key)
    if node is None:
        counts = tuple(int(i == state) for i in range(num_states))
        node = _Node(0, state, (), counts)
        _INTERN[key] = node
    return node


def _empty(level: int, ndim: int, num_states: int) -> _Node:
    if level == 0:
        return _leaf(0, num_states)
    key = (ndim, level, "empty", num_states)
    node = _INTERN.get(key)
    if node is None:
        children = (_empty(level - 1, ndim, num_states),) * (2**ndim)
        node = _Node(level, None, children, (0,) * num_states)
        _INTERN[key] = node
    return node


def _branch(level: int, children: tuple[_Node, ...], ndim: int, num_states: int) -> _Node:
    if all(child is _empty(level - 1, ndim, num_states) for child in children):
        return _empty(level, ndim, num_states)
    key = (ndim, level, tuple(id(child) for child in children), num_states)
    node = _INTERN.get(key)
    if node is None:
        counts = tuple(sum(child.counts[state] for child in children) for state in range(num_states))
        node = _Node(level, None, children, counts)
        _INTERN[key] = node
    return node


def _build_tree(
    cells: Mapping[tuple[int, ...], int], origin: tuple[int, ...], level: int,
    ndim: int, num_states: int,
) -> _Node:
    if not cells:
        return _empty(level, ndim, num_states)
    if level == 0:
        return _leaf(next(iter(cells.values())), num_states)

    half = 1 << (level - 1)
    partitions: list[dict[tuple[int, ...], int]] = [dict() for _ in range(2**ndim)]
    for coord, state in cells.items():
        bits = tuple(int(coord[axis] >= origin[axis] + half) for axis in range(ndim))
        child_index = bits[0] if ndim == 1 else bits[0] * 2 + bits[1]
        partitions[child_index][coord] = state

    children = []
    for index, partition in enumerate(partitions):
        bits = (index,) if ndim == 1 else (index // 2, index % 2)
        child_origin = tuple(origin[axis] + bits[axis] * half for axis in range(ndim))
        children.append(_build_tree(partition, child_origin, level - 1, ndim, num_states))
    return _branch(level, tuple(children), ndim, num_states)


def _coordinates(value: Sequence[int], ndim: int, what: str) -> tuple[int, ...]:
    if len(value) != ndim:
        raise ValueError(f"{what} needs {ndim} coordinate values")
    if any(isinstance(v, bool) or not isinstance(v, Integral) for v in value):
        raise ValueError(f"{what} coordinates must be integers")
    return tuple(int(v) for v in value)


class TreeUniverse(Universe):
    """An immutable sparse universe backed by a canonical binary tree or quadtree.

    Construct with :meth:`from_sparse`, passing a mapping of coordinates to
    non-zero state ids (or an iterable of ``(coordinate, state)`` pairs). `bounds`
    is a finite shape in NumPy axis order, or ``None`` for an unbounded world.
    """

    def __init__(
        self,
        automaton: Automaton,
        cells: Mapping[tuple[int, ...], int],
        bounds: tuple[int, ...] | None = None,
        generation: int = 0,
    ) -> None:
        if generation < 0:
            raise ValueError("generation must be >= 0")
        ndim = automaton.neighborhood.ndim
        if ndim not in (1, 2):
            raise ValueError("TreeUniverse supports only 1D and 2D automata")
        if not automaton.is_deterministic:
            raise ValueError("TreeUniverse requires a deterministic automaton")
        if not automaton.is_quiescent():
            raise ValueError("TreeUniverse requires a quiescent state-0 background")
        if automaton.neighborhood.reach > 1:
            raise ValueError("TreeUniverse supports neighborhoods with reach at most 1")

        if bounds is not None:
            bounds = _coordinates(bounds, ndim, "bounds")
            if any(size <= 0 for size in bounds):
                raise ValueError("finite bounds must be positive")
            if not isinstance(automaton.boundary, (Periodic, Fixed)):
                raise ValueError("TreeUniverse supports only Periodic and Fixed(0) finite boundaries")
            if isinstance(automaton.boundary, Fixed) and automaton.boundary.value != 0:
                raise ValueError("TreeUniverse supports only Fixed(0) finite boundaries")

        checked: dict[tuple[int, ...], int] = {}
        for raw_coord, raw_state in cells.items():
            coord = _coordinates(raw_coord, ndim, "cell")
            if isinstance(raw_state, bool) or not isinstance(raw_state, Integral):
                raise ValueError("cell states must be integers")
            state = int(raw_state)
            if not 0 <= state < automaton.num_states:
                raise ValueError(f"cell state {state} is outside [0, {automaton.num_states})")
            if state == 0:
                raise ValueError("sparse cells must have non-zero states")
            if bounds is not None and any(c < 0 or c >= size for c, size in zip(coord, bounds)):
                raise ValueError(f"cell coordinate {coord} is outside finite bounds {bounds}")
            if coord in checked:
                raise ValueError(f"duplicate sparse coordinate {coord}")
            checked[coord] = state

        self.automaton = automaton
        self.generation = generation
        self._bounds = bounds
        self._ndim = ndim
        self._origin, self._level = self._tree_extent(checked, bounds, ndim)
        self._root = _build_tree(checked, self._origin, self._level, ndim, automaton.num_states)

    @classmethod
    def from_sparse(
        cls,
        automaton: Automaton,
        cells: Mapping[Sequence[int], int] | Iterable[tuple[Sequence[int], int]],
        bounds: tuple[int, ...] | None = None,
        generation: int = 0,
    ) -> TreeUniverse:
        """Create a universe from non-zero coordinate/state pairs."""
        if isinstance(cells, Mapping):
            items = cells.items()
        else:
            items = cells
        data: dict[tuple[int, ...], int] = {}
        ndim = automaton.neighborhood.ndim
        for coord, state in items:
            key = _coordinates(coord, ndim, "cell")
            if key in data:
                raise ValueError(f"duplicate sparse coordinate {key}")
            data[key] = state
        return cls(automaton, data, bounds, generation)

    @classmethod
    def from_coordinates(cls, *args, **kwargs) -> TreeUniverse:
        """Alias for :meth:`from_sparse`, suitable for coordinate-oriented callers."""
        return cls.from_sparse(*args, **kwargs)

    @staticmethod
    def _tree_extent(
        cells: Mapping[tuple[int, ...], int], bounds: tuple[int, ...] | None, ndim: int,
    ) -> tuple[tuple[int, ...], int]:
        if bounds is not None:
            extent = max(bounds)
            level = max(0, (extent - 1).bit_length())
            return (0,) * ndim, level
        if not cells:
            return (0,) * ndim, 0
        minima = tuple(min(coord[axis] for coord in cells) for axis in range(ndim))
        maxima = tuple(max(coord[axis] for coord in cells) for axis in range(ndim))
        extent = max(maximum - minimum + 1 for minimum, maximum in zip(minima, maxima))
        level = max(0, (extent - 1).bit_length())
        return minima, level

    @property
    def root(self) -> _Node:
        """Canonical tree root, exposed for storage adapters and sharing inspection."""
        return self._root

    @property
    def bounds(self) -> tuple[int, ...] | None:
        return self._bounds

    def iter_nonzero(self) -> Iterator[tuple[tuple[int, ...], int]]:
        """Yield ``(coordinate, state)`` pairs in lexicographic coordinate order."""
        yield from sorted(self._walk(self._root, self._origin, self._level))

    def _walk(self, node: _Node, origin: tuple[int, ...], level: int) -> Iterator[tuple[tuple[int, ...], int]]:
        if not any(node.counts[1:]):
            return
        if level == 0:
            yield origin, node.state
            return
        half = 1 << (level - 1)
        for index, child in enumerate(node.children):
            bits = (index,) if self._ndim == 1 else (index // 2, index % 2)
            child_origin = tuple(origin[axis] + bits[axis] * half for axis in range(self._ndim))
            yield from self._walk(child, child_origin, level - 1)

    def _nonzero_map(self) -> dict[tuple[int, ...], int]:
        return dict(self.iter_nonzero())

    def export_sparse(self) -> dict[tuple[int, ...], int]:
        """Return a detached sparse snapshot suitable for persistence."""
        return self._nonzero_map()

    def counts(self) -> dict[int, int]:
        counts = {state: int(self._root.counts[state]) for state in range(1, self.automaton.num_states)}
        if self._bounds is not None:
            counts[0] = int(np.prod(self._bounds, dtype=object)) - sum(counts.values())
            return {state: counts[state] for state in range(self.automaton.num_states)}
        return counts

    def window(self, start: Sequence[int], shape: Sequence[int]) -> np.ndarray:
        start = _coordinates(start, self._ndim, "start")
        shape = _coordinates(shape, self._ndim, "shape")
        if any(size < 0 for size in shape):
            raise ValueError("window shape values must be non-negative")
        if self._bounds is not None and any(
            begin < 0 or begin + size > bound for begin, size, bound in zip(start, shape, self._bounds)
        ):
            raise ValueError(f"window {start} + {shape} is outside finite bounds {self._bounds}")
        out = np.zeros(shape, dtype=DTYPE)
        self._copy_window(self._root, self._origin, self._level, start, shape, out)
        return out

    def _copy_window(
        self,
        node: _Node,
        origin: tuple[int, ...],
        level: int,
        start: tuple[int, ...],
        shape: tuple[int, ...],
        out: np.ndarray,
    ) -> None:
        if not any(node.counts[1:]):
            return
        block_size = 1 << level
        if any(
            origin[axis] >= start[axis] + shape[axis]
            or origin[axis] + block_size <= start[axis]
            for axis in range(self._ndim)
        ):
            return
        if level == 0:
            local = tuple(coord - base for coord, base in zip(origin, start))
            out[local] = node.state
            return

        half = 1 << (level - 1)
        for index, child in enumerate(node.children):
            bits = (index,) if self._ndim == 1 else (index // 2, index % 2)
            child_origin = tuple(origin[axis] + bits[axis] * half for axis in range(self._ndim))
            self._copy_window(child, child_origin, level - 1, start, shape, out)

    def fingerprint(self) -> str:
        h = hashlib.blake2b(digest_size=16)
        h.update(b"tree-universe-v1")
        h.update(repr((self._ndim, self._bounds)).encode())
        for coord, state in self.iter_nonzero():
            h.update(repr(coord).encode())
            h.update(bytes((state,)))
        return h.hexdigest()

    def step(self, n: int = 1, rng: np.random.Generator | None = None) -> TreeUniverse:
        if n < 0:
            raise ValueError("n must be >= 0")
        universe = self
        for _ in range(n):
            universe = universe._step_once(rng)
        return universe

    def _step_once(self, rng: np.random.Generator | None) -> TreeUniverse:
        offsets = self.automaton.neighborhood.offsets()
        support = self._nonzero_map()
        candidates: set[tuple[int, ...]] = set(support)
        for coord in support:
            for offset in offsets:
                target = tuple(c - d for c, d in zip(coord, offset))
                if self._bounds is not None:
                    if isinstance(self.automaton.boundary, Periodic):
                        target = tuple(c % size for c, size in zip(target, self._bounds))
                    elif any(c < 0 or c >= size for c, size in zip(target, self._bounds)):
                        continue
                candidates.add(target)

        coords = sorted(candidates)
        current = np.fromiter((support.get(coord, 0) for coord in coords), dtype=DTYPE, count=len(coords))
        neighbours = np.zeros((len(coords), len(offsets)), dtype=DTYPE)
        for column, offset in enumerate(offsets):
            for row, coord in enumerate(coords):
                source = tuple(c + d for c, d in zip(coord, offset))
                if self._bounds is not None and isinstance(self.automaton.boundary, Periodic):
                    source = tuple(c % size for c, size in zip(source, self._bounds))
                if self._bounds is None or all(0 <= c < size for c, size in zip(source, self._bounds)):
                    neighbours[row, column] = support.get(source, 0)

        result = self.automaton.rule.apply(current, neighbours, self.automaton.num_states, rng)
        next_cells = {coord: int(state) for coord, state in zip(coords, result) if state != 0}
        return TreeUniverse(self.automaton, next_cells, self._bounds, self.generation + 1)

    def __repr__(self) -> str:
        world = "infinite" if self._bounds is None else f"bounds={self._bounds}"
        return f"TreeUniverse({self.automaton.name!r}, generation={self.generation}, {world}, nonzero={sum(self._root.counts[1:])})"
