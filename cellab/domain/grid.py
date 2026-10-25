"""Grid (one immutable board) and CellType."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

# Every grid is stored with the same dtype so that equal boards always hash equally.
DTYPE = np.uint8
MAX_STATES = int(np.iinfo(DTYPE).max) + 1  # 256


@dataclass(frozen=True)
class CellType:
    """A named cell state. Its integer value is its position in Automaton.cell_types."""

    name: str


class Grid:
    """An immutable n-dimensional array of cell-state indices."""

    __slots__ = ("_cells", "_digest")

    def __init__(self, cells) -> None:
        arr = np.asarray(cells)
        if arr.ndim == 0:
            raise ValueError("a grid needs at least one dimension")
        if not (np.issubdtype(arr.dtype, np.integer) or arr.dtype == np.bool_):
            raise TypeError(f"grid cells must be integers, got {arr.dtype}")
        if arr.size and (arr.min() < 0 or arr.max() >= MAX_STATES):
            raise ValueError(f"cell values must be in [0, {MAX_STATES})")
        arr = np.array(arr, dtype=DTYPE)  # always a private copy
        arr.flags.writeable = False
        self._cells = arr
        self._digest: str | None = None

    # --- basic properties -------------------------------------------------
    @property
    def cells(self) -> np.ndarray:
        """Read-only view of the underlying array."""
        return self._cells

    @property
    def shape(self) -> tuple[int, ...]:
        return self._cells.shape

    @property
    def ndim(self) -> int:
        return self._cells.ndim

    # --- identity ---------------------------------------------------------
    def hash(self) -> str:
        """Stable content hash (same board -> same string, across runs and machines)."""
        if self._digest is None:
            h = hashlib.blake2b(digest_size=16)
            h.update(repr(self.shape).encode())
            h.update(self._cells.tobytes())
            self._digest = h.hexdigest()
        return self._digest

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Grid):
            return NotImplemented
        return self.shape == other.shape and np.array_equal(self._cells, other._cells)

    def __hash__(self) -> int:
        return hash(self.hash())

    # --- statistics -------------------------------------------------------
    def count(self, state: int) -> int:
        return int(np.count_nonzero(self._cells == state))

    def counts(self, num_states: int) -> np.ndarray:
        """Number of cells in each state 0..num_states-1."""
        return np.bincount(self._cells.ravel(), minlength=num_states)

    # --- text helpers (handy for tests and debugging; states 0-9 only) ----
    @classmethod
    def from_text(cls, text: str) -> Grid:
        """'0010' -> 1D grid; several lines of digits -> 2D grid."""
        rows = [line.strip().replace(" ", "") for line in text.strip().splitlines()]
        rows = [r for r in rows if r]
        data = [[int(ch) for ch in r] for r in rows]
        return cls(data[0] if len(data) == 1 else data)

    def to_text(self) -> str:
        if self.ndim == 1:
            return "".join(map(str, self._cells))
        if self.ndim == 2:
            return "\n".join("".join(map(str, row)) for row in self._cells)
        raise ValueError("to_text supports 1D and 2D grids only")

    def __repr__(self) -> str:
        return f"Grid(shape={self.shape}, hash={self.hash()[:8]})"