"""In-memory orchestration around the existing cellular automata domain."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import numpy as np
import yaml

try:
    from cellab.domain import ArrayUniverse, Grid, TreeUniverse, Universe
    from cellab.domain.presets import cyclic, elementary, forest_fire, game_of_life
except ModuleNotFoundError:  # Support the repository's existing `cd cellab` workflow.
    from domain import ArrayUniverse, Grid, TreeUniverse, Universe
    from domain.presets import cyclic, elementary, forest_fire, game_of_life

CYCLIC_COLORS = (
    "#e63946", "#f4a261", "#2a9d8f", "#457b9d", "#9b5de5", "#f15bb5",
    "#00bbf9", "#fee440", "#70e000", "#ff6d00",
)


@dataclass
class SimulationRun:
    id: str
    preset: str
    one_dimensional: bool
    universe: Universe
    rng: np.random.Generator
    counts: list[int | None]
    config_yaml: str
    config_hash: str
    display_name: str
    infinite: bool
    viewport: dict[str, int] | None = None


class SimulationService:
    """Owns live runs; state is intentionally process-local for the MVP."""

    MAX_VIEWPORT_PADDING = 64
    ESTIMATED_ARRAY_BYTES_PER_CELL = 32
    DEFAULT_TREE_THRESHOLD_MB = 150
    MAX_TREE_CELLS = 100_000

    _STATE_COLORS = {
        "dead": "#ffffff",
        "alive": "#111111",
        "empty": "#ffffff",
        "tree": "#228b22",
        "burning": "#ff4500",
    }
    _PRESETS: dict[str, dict[str, Any]] = {
        "game_of_life": {
            "display_name": "Game of Life",
            "dimensionality": 2,
            "states": (("dead", "#ffffff"), ("alive", "#111111")),
            "parameters": {},
        },
        "elementary": {
            "display_name": "Elementary (Wolfram rule)",
            "dimensionality": 1,
            "states": (("dead", "#ffffff"), ("alive", "#111111")),
            "parameters": {
                "rule": {"type": "integer", "default": 30, "minimum": 0, "maximum": 255}
            },
        },
        "cyclic": {
            "display_name": "Cyclic",
            "dimensionality": 2,
            "states": tuple((f"s{i}", CYCLIC_COLORS[i % len(CYCLIC_COLORS)]) for i in range(4)),
            "parameters": {
                "num_states": {"type": "integer", "default": 4, "minimum": 2, "maximum": 256},
                "threshold": {"type": "integer", "default": 2, "minimum": 0},
            },
        },
        "forest_fire": {
            "display_name": "Forest Fire",
            "dimensionality": 2,
            "states": (("empty", "#ffffff"), ("tree", "#228b22"), ("burning", "#ff4500")),
            "parameters": {
                "growth": {"type": "number", "default": 0.05, "minimum": 0.0, "maximum": 1.0},
                "lightning": {"type": "number", "default": 0.001, "minimum": 0.0, "maximum": 1.0},
            },
        },
    }

    def __init__(self) -> None:
        self._runs: dict[str, SimulationRun] = {}

    def preset_metadata(self) -> list[dict[str, Any]]:
        return [
            {
                "id": preset_id,
                "display_name": data["display_name"],
                "dimensionality": data["dimensionality"],
                "states": [{"name": name, "color": color} for name, color in data["states"]],
                "parameters": data["parameters"],
            }
            for preset_id, data in self._PRESETS.items()
        ]

    def create(self, config: dict[str, Any]) -> dict[str, Any]:
        run = self._new_run(config)
        self._runs[run.id] = run
        return self._frame(run)

    def reset(self, simulation_id: str, config: dict[str, Any]) -> dict[str, Any]:
        if simulation_id not in self._runs:
            raise KeyError(simulation_id)
        run = self._new_run(config, simulation_id=simulation_id)
        self._runs[simulation_id] = run
        return self._frame(run)

    def frame(self, simulation_id: str) -> dict[str, Any]:
        return self._frame(self._get_run(simulation_id))

    def step(self, simulation_id: str) -> dict[str, Any]:
        run = self._get_run(simulation_id)
        automaton = run.universe.automaton
        if isinstance(run.universe, TreeUniverse):
            run.universe = run.universe.step(rng=run.rng)
            run.counts = self._counts_list(run.universe)
        else:
            next_grid, next_counts = self._step_chunked(automaton, run.universe.grid, run.rng)
            run.universe = ArrayUniverse(
                automaton,
                next_grid,
                run.universe.generation + 1,
                check=False,
            )
            run.counts = next_counts
        return self._frame(run)

    def set_viewport(self, simulation_id: str, viewport: dict[str, Any]) -> dict[str, Any]:
        run = self._get_run(simulation_id)
        run.viewport = self._normalize_viewport(run, viewport)
        return self._frame(run)

    def paint_cells(self, simulation_id: str, cells: list[dict[str, Any]]) -> dict[str, Any]:
        run = self._get_run(simulation_id)
        width, height = self._dimensions(run)
        counts = run.counts.copy()
        if isinstance(run.universe, TreeUniverse):
            updated_sparse = run.universe.export_sparse()
            updated: Any = updated_sparse
        else:
            updated = run.universe.grid.cells.copy()
        for index, cell in enumerate(cells):
            if not isinstance(cell, dict):
                raise ValueError(f"cells[{index}] must be an object")
            x, y, state = cell.get("x"), cell.get("y"), cell.get("state")
            for name, value in (("x", x), ("y", y), ("state", state)):
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError(f"cells[{index}].{name} must be an integer")
            if not run.infinite and (not 0 <= x < width or not 0 <= y < height):
                raise ValueError(f"cells[{index}] coordinates must be inside the world")
            if not 0 <= state < run.universe.automaton.num_states:
                raise ValueError(f"cells[{index}].state must be a valid state ID")
            if run.one_dimensional:
                coordinate = (x,)
                previous = int(updated.get(coordinate, 0)) if run.infinite or isinstance(run.universe, TreeUniverse) else int(updated[x])
                if isinstance(run.universe, TreeUniverse):
                    if state == 0:
                        updated.pop(coordinate, None)
                    else:
                        updated[coordinate] = state
                else:
                    updated[x] = state
            else:
                coordinate = (y, x)
                previous = int(updated.get(coordinate, 0)) if run.infinite or isinstance(run.universe, TreeUniverse) else int(updated[y, x])
                if isinstance(run.universe, TreeUniverse):
                    if state == 0:
                        updated.pop(coordinate, None)
                    else:
                        updated[coordinate] = state
                else:
                    updated[y, x] = state
            if previous != state:
                if counts[previous] is not None:
                    counts[previous] -= 1
                if counts[state] is not None:
                    counts[state] += 1
        if isinstance(run.universe, TreeUniverse):
            run.universe = TreeUniverse.from_sparse(
                run.universe.automaton,
                updated,
                bounds=run.universe.bounds,
                generation=run.universe.generation,
            )
        else:
            run.universe = ArrayUniverse(
                run.universe.automaton,
                Grid._trusted(updated),
                run.universe.generation,
                check=False,
            )
        run.counts = counts
        return self._frame(run)

    def clear(self, simulation_id: str) -> dict[str, Any]:
        run = self._get_run(simulation_id)
        if isinstance(run.universe, TreeUniverse):
            run.universe = TreeUniverse.from_sparse(
                run.universe.automaton,
                {},
                bounds=run.universe.bounds,
                generation=run.universe.generation,
            )
            if run.infinite:
                run.counts = [None, *([0] * (run.universe.automaton.num_states - 1))]
            else:
                run.counts = [int(np.prod(run.universe.bounds, dtype=object)), *([0] * (run.universe.automaton.num_states - 1))]
        else:
            cleared = Grid._trusted(np.zeros(run.universe.grid.shape, dtype=np.uint8))
            run.universe = ArrayUniverse(
                run.universe.automaton,
                cleared,
                run.universe.generation,
                check=False,
            )
            run.counts = [int(cleared.cells.size), *([0] * (run.universe.automaton.num_states - 1))]
        return self._frame(run)

    def replace_grid(self, simulation_id: str, rows: list[list[int]]) -> dict[str, Any]:
        run = self._get_run(simulation_id)
        if isinstance(run.universe, TreeUniverse):
            raise ValueError("full-grid replacement is unavailable for tree-backed worlds; paint cells through the viewport")
        width, height = self._dimensions(run)
        if len(rows) != height or any(len(row) != width for row in rows):
            raise ValueError(f"grid must have {height} rows of {width} cells")
        try:
            grid = Grid(rows[0] if run.one_dimensional else rows)
            run.universe.automaton.check_grid(grid)
        except (TypeError, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        run.universe = ArrayUniverse(run.universe.automaton, grid, run.universe.generation)
        run.counts = [int(count) for count in grid.counts(run.universe.automaton.num_states)]
        return self._frame(run)

    def _new_run(self, config: dict[str, Any], simulation_id: str | None = None) -> SimulationRun:
        preset = config["preset"]
        if preset not in self._PRESETS:
            raise ValueError(f"unknown preset {preset!r}; expected one of {', '.join(self._PRESETS)}")
        parameters = config.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise ValueError("parameters must be an object")
        allowed_parameters = self._PRESETS[preset]["parameters"]
        unknown = parameters.keys() - allowed_parameters.keys()
        if unknown:
            raise ValueError(f"unsupported parameter(s) for {preset}: {', '.join(sorted(unknown))}")
        params = {name: spec["default"] for name, spec in allowed_parameters.items()}
        params.update(parameters)
        self._validate_parameters(preset, params)

        infinite = config.get("infinite", False)
        if not isinstance(infinite, bool):
            raise ValueError("infinite must be a boolean")

        if preset == "elementary":
            automaton = elementary(params["rule"])
            one_dimensional = True
        else:
            one_dimensional = False
            if preset == "game_of_life":
                automaton = game_of_life()
            elif preset == "cyclic":
                automaton = cyclic(num_states=params["num_states"], threshold=params["threshold"])
            else:
                automaton = forest_fire(growth=params["growth"], lightning=params["lightning"])

        width = config.get("width")
        height = config.get("height")
        if infinite:
            if width is not None or height is not None:
                raise ValueError("infinite worlds must not specify width or height")
            if config["density"] != 0:
                raise ValueError("infinite worlds require density 0; provide finite initial_cells instead")
            shape = None
        else:
            if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
                raise ValueError("width must be a positive integer for finite worlds")
            if one_dimensional:
                height = 1
                shape = (width,)
            else:
                if isinstance(height, bool) or not isinstance(height, int) or height <= 0:
                    raise ValueError("height must be a positive integer for finite worlds")
                shape = (height, width)

        rng = np.random.default_rng(config.get("seed"))
        density = config["density"]
        initial_cells = self._normalize_initial_cells(
            config.get("initial_cells") or [], one_dimensional, width, height, infinite, automaton.num_states
        )
        threshold_bytes = self._tree_threshold_mb() * 1024 * 1024
        world_cells = None if infinite else int(np.prod(shape, dtype=object))
        if world_cells is not None and world_cells > 2**63 - 1:
            raise ValueError("finite world is larger than the supported signed 64-bit coordinate range")
        use_tree = infinite or config.get("_force_tree", False) or (
            not config.get("_force_array", False)
            and world_cells * self.ESTIMATED_ARRAY_BYTES_PER_CELL > threshold_bytes
        )

        if use_tree:
            tree_cells: dict[tuple[int, ...], int] = {}
            if density > 0:
                assert world_cells is not None
                if world_cells > 2**63 - 1:
                    raise ValueError("finite world is too large for seeded sparse initialization")
                expected_active = world_cells * density
                max_tree_cells = int(os.environ.get("CELLAB_MAX_TREE_CELLS", str(self.MAX_TREE_CELLS)))
                active_count = int(rng.binomial(world_cells, density))
                if active_count + len(tree_cells) > max_tree_cells:
                    raise ValueError(
                        "tree storage is for sparse worlds; reduce density or provide a sparse initial_cells pattern "
                        f"(estimated {expected_active:,.0f} random active cells)"
                    )
                if active_count:
                    flat_indices = rng.choice(world_cells, size=active_count, replace=False)
                    states = rng.integers(1, automaton.num_states, size=active_count)
                    for flat_index, state in zip(flat_indices, states):
                        coord = (int(flat_index),) if one_dimensional else (int(flat_index // width), int(flat_index % width))
                        tree_cells[coord] = int(state)
            for coord, state in initial_cells.items():
                if state == 0:
                    tree_cells.pop(coord, None)
                else:
                    tree_cells[coord] = state
            max_tree_cells = int(os.environ.get("CELLAB_MAX_TREE_CELLS", str(self.MAX_TREE_CELLS)))
            if len(tree_cells) > max_tree_cells:
                raise ValueError(f"tree worlds support at most {max_tree_cells:,} explicit non-zero cells")
            universe: Universe = TreeUniverse.from_sparse(
                automaton,
                tree_cells,
                bounds=None if infinite else shape,
            )
            counts = self._counts_list(universe)
        else:
            cells = np.zeros(shape, dtype=np.uint8)
            flat = cells.reshape(-1)
            state_counts = np.zeros(automaton.num_states, dtype=np.int64)
            chunk_size = 1_000_000
            if density > 0:
                for start in range(0, flat.size, chunk_size):
                    target = flat[start : start + chunk_size]
                    active = rng.random(target.size) < density
                    active_count = int(active.sum())
                    state_counts[0] += target.size - active_count
                    if automaton.num_states == 2:
                        target[active] = 1
                        state_counts[1] += active_count
                    elif active.any():
                        selected = rng.integers(1, automaton.num_states, size=active_count, dtype=np.uint8)
                        target[active] = selected
                        state_counts += np.bincount(selected, minlength=automaton.num_states)
            else:
                state_counts[0] = flat.size
            for coord, state in initial_cells.items():
                index = coord[0] if one_dimensional else coord
                previous = int(cells[index]) if one_dimensional else int(cells[index[0], index[1]])
                if one_dimensional:
                    cells[index] = state
                else:
                    cells[index[0], index[1]] = state
                state_counts[previous] -= 1
                state_counts[state] += 1
            universe = ArrayUniverse(automaton, Grid._trusted(cells), check=False)
            counts = [int(count) for count in state_counts]

        config_yaml = config.get("config_yaml")
        name = str(config.get("name") or self._PRESETS[preset]["display_name"])
        if config_yaml is not None:
            if not isinstance(config_yaml, str):
                raise ValueError("config_yaml must be text")
            config_hash = hashlib.sha256(config_yaml.encode("utf-8")).hexdigest()
            if config.get("config_hash") not in (None, config_hash):
                raise ValueError("config_hash does not match config_yaml")
        else:
            config_yaml = self._make_config_yaml(
                preset, name, width, height, infinite, params, density, config.get("seed"),
                initial_cells, one_dimensional,
            )
            config_hash = hashlib.sha256(config_yaml.encode("utf-8")).hexdigest()
        run = SimulationRun(
            id=simulation_id or str(uuid4()),
            preset=preset,
            one_dimensional=one_dimensional,
            universe=universe,
            rng=rng,
            counts=counts,
            config_yaml=config_yaml,
            config_hash=config_hash,
            display_name=name,
            infinite=infinite,
        )
        if config.get("viewport") is not None:
            run.viewport = self._normalize_viewport(run, config["viewport"])
        elif use_tree:
            default_width, default_height = (min(width or 80, 80), 1 if one_dimensional else min(height or 40, 40))
            run.viewport = self._normalize_viewport(
                run,
                {"x": 0, "y": 0, "width": default_width, "height": default_height, "padding": 64},
            )
        return run

    @staticmethod
    def _tree_threshold_mb() -> int:
        try:
            threshold = int(os.environ.get("CELLAB_TREE_THRESHOLD_MB", str(SimulationService.DEFAULT_TREE_THRESHOLD_MB)))
        except ValueError as exc:
            raise ValueError("CELLAB_TREE_THRESHOLD_MB must be a positive integer") from exc
        if threshold <= 0:
            raise ValueError("CELLAB_TREE_THRESHOLD_MB must be a positive integer")
        return threshold

    @staticmethod
    def _normalize_initial_cells(
        cells: Any,
        one_dimensional: bool,
        width: int | None,
        height: int | None,
        infinite: bool,
        num_states: int,
    ) -> dict[tuple[int, ...], int]:
        if not isinstance(cells, list):
            raise ValueError("initial_cells must be an array")
        normalized: dict[tuple[int, ...], int] = {}
        for index, cell in enumerate(cells):
            if not isinstance(cell, dict):
                raise ValueError(f"initial_cells[{index}] must be an object")
            x, y, state = cell.get("x"), cell.get("y", 0), cell.get("state")
            for key, value in (("x", x), ("y", y), ("state", state)):
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError(f"initial_cells[{index}].{key} must be an integer")
            if not 0 <= state < num_states:
                raise ValueError(f"initial_cells[{index}].state is not a valid state")
            if one_dimensional:
                if y != 0 or (not infinite and not 0 <= x < width):
                    raise ValueError(f"initial_cells[{index}] is outside the 1D world")
                coord = (x,)
            else:
                if not infinite and not (0 <= x < width and 0 <= y < height):
                    raise ValueError(f"initial_cells[{index}] is outside the 2D world")
                coord = (y, x)
            if coord in normalized:
                raise ValueError(f"duplicate initial cell coordinate {coord}")
            normalized[coord] = state
        return normalized

    @staticmethod
    def _counts_list(universe: Universe) -> list[int | None]:
        counts = universe.counts()
        if universe.bounds is None:
            return [None, *[int(counts.get(state, 0)) for state in range(1, universe.automaton.num_states)]]
        return [int(counts.get(state, 0)) for state in range(universe.automaton.num_states)]

    @staticmethod
    def _make_config_yaml(
        preset: str,
        name: str,
        width: int | None,
        height: int | None,
        infinite: bool,
        parameters: dict[str, Any],
        density: float,
        seed: int | None,
        initial_cells: dict[tuple[int, ...], int],
        one_dimensional: bool,
    ) -> str:
        state_names = [f"s{index}" for index in range(parameters.get("num_states", 4))] if preset == "cyclic" else {
            "game_of_life": ["dead", "alive"],
            "elementary": ["dead", "alive"],
            "forest_fire": ["empty", "tree", "burning"],
        }[preset]
        cells_by_state: dict[str, list[list[int]]] = {}
        for coord, state in sorted(initial_cells.items()):
            state_name = state_names[state]
            coordinate = [coord[0]] if one_dimensional else [coord[0], coord[1]]
            cells_by_state.setdefault(state_name, []).append(coordinate)
        world = {"infinite": True} if infinite else {
            "width": width,
            **({"height": 1} if one_dimensional else {"height": height}),
            "infinite": False,
        }
        document = {
            "version": 1,
            "name": name,
            "preset": preset,
            "world": world,
            "parameters": parameters,
            "initial": {"density": density, "seed": seed, "cells": cells_by_state},
        }
        return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)

    @staticmethod
    def _validate_parameters(preset: str, params: dict[str, Any]) -> None:
        def integer(name: str, minimum: int, maximum: int | None = None) -> int:
            value = params[name]
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"parameter {name!r} must be an integer")
            if value < minimum or (maximum is not None and value > maximum):
                bounds = f"at least {minimum}" if maximum is None else f"between {minimum} and {maximum}"
                raise ValueError(f"parameter {name!r} must be {bounds}")
            return value

        def probability(name: str) -> float:
            value = params[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"parameter {name!r} must be a number")
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"parameter {name!r} must be between 0 and 1")
            return float(value)

        if preset == "elementary":
            integer("rule", 0, 255)
        elif preset == "cyclic":
            integer("num_states", 2, 256)
            integer("threshold", 0)
        elif preset == "forest_fire":
            probability("growth")
            probability("lightning")

    def _get_run(self, simulation_id: str) -> SimulationRun:
        try:
            return self._runs[simulation_id]
        except KeyError:
            raise KeyError(simulation_id) from None

    @staticmethod
    def _dimensions(run: SimulationRun) -> tuple[int | None, int | None]:
        bounds = run.universe.bounds
        if bounds is None:
            return None, None
        if run.one_dimensional:
            return bounds[0], 1
        height, width = bounds
        return width, height

    @classmethod
    def _normalize_viewport(cls, run: SimulationRun, viewport: dict[str, Any]) -> dict[str, int]:
        if not isinstance(viewport, dict):
            raise ValueError("viewport must be an object")
        required = ("x", "y", "width", "height", "padding")
        values = {key: viewport.get(key) for key in required}
        for key, value in values.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"viewport.{key} must be an integer")
        if values["width"] <= 0 or values["height"] <= 0:
            raise ValueError("viewport width and height must be positive")
        if values["padding"] < 0:
            raise ValueError("viewport padding must be non-negative")

        world_width, world_height = cls._dimensions(run)
        if run.infinite:
            x, width = values["x"], values["width"]
            if run.one_dimensional:
                y, height = 0, 1
            else:
                y, height = values["y"], values["height"]
        elif run.one_dimensional:
            x = min(max(values["x"], 0), world_width - 1)
            width = min(values["width"], world_width - x)
            y, height = 0, 1
        else:
            x = min(max(values["x"], 0), world_width - 1)
            width = min(values["width"], world_width - x)
            y = min(max(values["y"], 0), world_height - 1)
            height = min(values["height"], world_height - y)
        return {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "padding": min(values["padding"], cls.MAX_VIEWPORT_PADDING),
        }

    def _frame(self, run: SimulationRun) -> dict[str, Any]:
        width, height = self._dimensions(run)
        if run.viewport is not None:
            viewport = run.viewport
        elif isinstance(run.universe, ArrayUniverse):
            viewport = {"x": 0, "y": 0, "width": width, "height": height, "padding": 0}
        else:
            viewport = {
                "x": 0, "y": 0,
                "width": min(width or 80, 80),
                "height": 1 if run.one_dimensional else min(height or 40, 40),
                "padding": self.MAX_VIEWPORT_PADDING,
            }
        view_x, view_y = viewport["x"], viewport["y"]
        view_width, view_height = viewport["width"], viewport["height"]
        padding = viewport["padding"]
        if run.infinite:
            origin_x = view_x - padding
            origin_y = 0 if run.one_dimensional else view_y - padding
            tile_width = view_width + padding * 2
            tile_height = 1 if run.one_dimensional else view_height + padding * 2
        else:
            origin_x = max(0, view_x - padding)
            origin_y = 0 if run.one_dimensional else max(0, view_y - padding)
            end_x = min(width, view_x + view_width + padding)
            end_y = 1 if run.one_dimensional else min(height, view_y + view_height + padding)
            tile_width = end_x - origin_x
            tile_height = end_y - origin_y
        if run.one_dimensional:
            if isinstance(run.universe, TreeUniverse):
                rows = [run.universe.window((origin_x,), (tile_width,)).tolist()]
            else:
                rows = [run.universe.grid.cells[origin_x : origin_x + tile_width].tolist()]
        else:
            if isinstance(run.universe, TreeUniverse):
                rows = run.universe.window((origin_y, origin_x), (tile_height, tile_width)).tolist()
            else:
                rows = run.universe.grid.cells[origin_y : origin_y + tile_height, origin_x : origin_x + tile_width].tolist()
        return {
            "id": run.id,
            "preset": run.preset,
            "generation": run.universe.generation,
            "width": width,
            "height": height,
            "infinite": run.infinite,
            "one_dimensional": run.one_dimensional,
            "config_hash": run.config_hash,
            "states": [
                {"name": cell_type.name, "color": self._color(run.preset, index, cell_type.name)}
                for index, cell_type in enumerate(run.universe.automaton.cell_types)
            ],
            "view": {"x": view_x, "y": view_y, "width": view_width, "height": view_height},
            "grid_origin": {"x": origin_x, "y": origin_y},
            "counts": run.counts,
            "grid": rows,
        }

    def export_config(self, simulation_id: str) -> tuple[str, str]:
        run = self._get_run(simulation_id)
        return run.config_yaml, run.config_hash

    def export_snapshot(self, simulation_id: str) -> dict[str, Any]:
        run = self._get_run(simulation_id)
        if isinstance(run.universe, TreeUniverse):
            state_format = "sparse-tree-json-v1"
            state_blob = json.dumps(
                {
                    "bounds": run.universe.bounds,
                    "generation": run.universe.generation,
                    "cells": [[list(coord), state] for coord, state in run.universe.iter_nonzero()],
                },
                separators=(",", ":"),
            ).encode("utf-8")
        else:
            state_format = "dense-uint8-v1"
            state_blob = run.universe.grid.cells.tobytes(order="C")
        width, height = self._dimensions(run)
        return {
            "config_hash": run.config_hash,
            "config_yaml": run.config_yaml,
            "preset": run.preset,
            "width": width,
            "height": height,
            "counts": run.counts,
            "generation": run.universe.generation,
            "state_format": state_format,
            "state_blob": state_blob,
        }

    def restore_snapshot(self, simulation_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        current = self._get_run(simulation_id)
        if snapshot.get("config_hash") != current.config_hash:
            raise ValueError("snapshot config hash does not match the active simulation")
        from .configs import parse_config_yaml

        parsed = parse_config_yaml(snapshot["config_yaml"].encode("utf-8"), self.preset_metadata())
        config = {
            key: parsed[key]
            for key in (
                "preset", "width", "height", "infinite", "parameters", "density", "seed",
                "initial_cells", "config_yaml", "config_hash", "name",
            )
        }
        config["_force_tree"] = snapshot["state_format"] == "sparse-tree-json-v1"
        config["_force_array"] = snapshot["state_format"] == "dense-uint8-v1"
        config["viewport"] = current.viewport or {
            "x": 0, "y": 0, "width": 80, "height": 1 if parsed["height"] == 1 else 40, "padding": 64,
        }
        run = self._new_run(config, simulation_id=simulation_id)
        generation = int(snapshot["generation"])
        state_format = snapshot["state_format"]
        if state_format == "dense-uint8-v1":
            if not isinstance(run.universe, ArrayUniverse):
                raise ValueError("dense snapshot does not match the config representation")
            expected_shape = run.universe.grid.shape
            data = np.frombuffer(snapshot["state_blob"], dtype=np.uint8)
            if data.size != int(np.prod(expected_shape, dtype=object)):
                raise ValueError("dense snapshot byte length does not match its config dimensions")
            grid = Grid._trusted(data.copy().reshape(expected_shape))
            run.universe = ArrayUniverse(run.universe.automaton, grid, generation, check=False)
        elif state_format == "sparse-tree-json-v1":
            if not isinstance(run.universe, TreeUniverse):
                raise ValueError("tree snapshot does not match the config representation")
            state = json.loads(snapshot["state_blob"].decode("utf-8"))
            bounds_value = state.get("bounds")
            bounds = None if bounds_value is None else tuple(bounds_value)
            sparse = {tuple(coord): int(value) for coord, value in state["cells"]}
            run.universe = TreeUniverse.from_sparse(
                run.universe.automaton,
                sparse,
                bounds=bounds,
                generation=generation,
            )
        else:
            raise ValueError(f"unsupported snapshot format {state_format!r}")
        run.counts = list(snapshot["counts"])
        self._runs[simulation_id] = run
        return self._frame(run)

    @classmethod
    def _color(cls, preset: str, index: int, name: str) -> str:
        if preset == "cyclic":
            return CYCLIC_COLORS[index % len(CYCLIC_COLORS)]
        return cls._STATE_COLORS[name]

    @staticmethod
    def _step_chunked(
        automaton,
        grid: Grid,
        rng: np.random.Generator,
        chunk_size: int = 256,
    ) -> tuple[Grid, list[int]]:
        """Step bounded worlds in tiles so neighbor buffers stay proportional to a tile.

        The input world remains complete and every cell advances. Each output tile
        gets a halo from the full prior generation, so cells on screen see exactly
        the same neighbours they would in one whole-array step.
        """
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        reach = automaton.neighborhood.reach
        padded = automaton.boundary.pad(grid.cells, reach)
        next_cells = np.empty_like(grid.cells)
        state_counts = np.zeros(automaton.num_states, dtype=np.int64)

        if grid.ndim == 1:
            length = grid.shape[0]
            for start in range(0, length, chunk_size):
                end = min(length, start + chunk_size)
                patch = padded[start : end + 2 * reach]
                stepped = automaton.step(Grid(patch), rng, check=False)
                core = stepped.cells[reach : reach + end - start]
                next_cells[start:end] = core
                state_counts += np.bincount(core, minlength=automaton.num_states)
        elif grid.ndim == 2:
            height, width = grid.shape
            for top in range(0, height, chunk_size):
                bottom = min(height, top + chunk_size)
                for left in range(0, width, chunk_size):
                    right = min(width, left + chunk_size)
                    patch = padded[top : bottom + 2 * reach, left : right + 2 * reach]
                    stepped = automaton.step(Grid(patch), rng, check=False)
                    core = stepped.cells[
                        reach : reach + bottom - top,
                        reach : reach + right - left,
                    ]
                    next_cells[top:bottom, left:right] = core
                    state_counts += np.bincount(core.ravel(), minlength=automaton.num_states)
        else:
            raise ValueError("only 1D and 2D worlds are supported")

        return Grid._trusted(next_cells), [int(count) for count in state_counts]
