"""Parsing and validation for uploaded CelLab run-config YAML files."""

from __future__ import annotations

import hashlib
import math
from typing import Any

import yaml


class ConfigError(ValueError):
    """A config file is malformed or does not match the v1 schema."""


class _StrictYamlLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: _StrictYamlLoader, node, deep: bool = False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                "mapping keys must be scalar values", key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                f"duplicate key {key!r}", key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictYamlLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def parse_config_yaml(data: bytes, presets: list[dict[str, Any]]) -> dict[str, Any]:
    """Parse exact uploaded bytes and return normalized run settings and metadata."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError("config must be UTF-8 YAML") from exc
    try:
        document = yaml.load(text, Loader=_StrictYamlLoader)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise ConfigError("config must be a YAML mapping")

    _keys(document, {"version", "name", "preset", "world", "parameters", "initial"}, "config")
    version = document.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise ConfigError("version must be 1")
    name = document.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError("name must be a non-empty string")

    preset_id = document.get("preset")
    preset_map = {item.get("id"): item for item in presets if isinstance(item, dict)}
    if not isinstance(preset_id, str) or preset_id not in preset_map:
        raise ConfigError(f"unknown preset {preset_id!r}")
    preset = preset_map[preset_id]

    world = document.get("world")
    if not isinstance(world, dict):
        raise ConfigError("world must be a mapping")
    _keys(world, {"width", "height", "infinite"}, "world")
    infinite = world.get("infinite", False)
    if not isinstance(infinite, bool):
        raise ConfigError("world.infinite must be a boolean")
    dimensionality = preset.get("dimensionality")
    if dimensionality not in (1, 2):
        raise ConfigError(f"preset {preset_id!r} has invalid dimensionality metadata")
    if infinite and preset_id == "forest_fire":
        raise ConfigError("infinite/tree worlds require a deterministic preset; forest_fire is stochastic")

    if infinite:
        if "width" in world or "height" in world:
            raise ConfigError("world.width and world.height must be absent for an infinite world")
        width = height = None
    else:
        width = _positive_int(world.get("width"), "world.width")
        if dimensionality == 1:
            if "height" in world:
                configured_height = world["height"]
                if isinstance(configured_height, bool) or not isinstance(configured_height, int) or configured_height != 1:
                    raise ConfigError("world.height for a 1D preset must be 1")
            height = 1
        else:
            height = _positive_int(world.get("height"), "world.height")

    parameters_value = document.get("parameters", {})
    if not isinstance(parameters_value, dict):
        raise ConfigError("parameters must be a mapping")
    parameter_specs = preset.get("parameters", {})
    if not isinstance(parameter_specs, dict):
        raise ConfigError("preset parameter metadata is invalid")
    unknown_parameters = set(parameters_value) - set(parameter_specs)
    if unknown_parameters:
        raise ConfigError(f"unsupported parameter(s) for {preset_id}: {', '.join(sorted(map(str, unknown_parameters)))}")
    parameters = {key: spec["default"] for key, spec in parameter_specs.items()}
    parameters.update(parameters_value)
    for key, value in parameters.items():
        _validate_parameter(key, value, parameter_specs[key])

    initial = document.get("initial")
    if not isinstance(initial, dict):
        raise ConfigError("initial must be a mapping")
    _keys(initial, {"density", "seed", "cells"}, "initial")
    density = initial.get("density", 0)
    if isinstance(density, bool) or not isinstance(density, (int, float)):
        raise ConfigError("initial.density must be a number")
    density = float(density)
    if not math.isfinite(density) or not 0 <= density <= 1:
        raise ConfigError("initial.density must be between 0 and 1")
    seed = initial.get("seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or seed < 0):
        raise ConfigError("initial.seed must be a non-negative integer")
    if infinite and density != 0:
        raise ConfigError("initial.density must be 0 for an infinite world")
    if infinite and "cells" not in initial:
        raise ConfigError("initial.cells is required for an infinite world")

    state_names = [state.get("name") for state in preset.get("states", [])]
    if preset_id == "cyclic":
        state_names = [f"s{index}" for index in range(parameters["num_states"])]
    if not state_names or any(not isinstance(state, str) for state in state_names):
        raise ConfigError(f"preset {preset_id!r} has invalid state metadata")
    state_ids = {state: index for index, state in enumerate(state_names)}
    initial_cells = _parse_cells(
        initial.get("cells", {}), state_ids, dimensionality, infinite, width, height
    )

    return {
        "preset": preset_id,
        "width": width,
        "height": height,
        "infinite": infinite,
        "parameters": parameters,
        "density": density,
        "seed": seed,
        "initial_cells": initial_cells,
        "config_yaml": text,
        "config_hash": hashlib.sha256(data).hexdigest(),
        "name": name,
    }


def _parse_cells(
    raw: Any,
    states: dict[str, int],
    dimensionality: int,
    infinite: bool,
    width: int | None,
    height: int | None,
) -> list[dict[str, int]]:
    if not isinstance(raw, dict):
        raise ConfigError("initial.cells must map state names to coordinate arrays")
    result: list[dict[str, int]] = []
    occupied: set[tuple[int, int]] = set()
    for state_name, coordinates in raw.items():
        if not isinstance(state_name, str) or state_name not in states:
            raise ConfigError(f"unknown initial.cells state {state_name!r}")
        if not isinstance(coordinates, list):
            raise ConfigError(f"initial.cells.{state_name} must be an array")
        for index, coordinate in enumerate(coordinates):
            if not isinstance(coordinate, list):
                raise ConfigError(f"initial.cells.{state_name}[{index}] must be an array")
            if dimensionality == 1:
                if len(coordinate) == 1:
                    row, col = 0, coordinate[0]
                elif len(coordinate) == 2:
                    row, col = coordinate
                    if row != 0:
                        raise ConfigError("1D cell coordinates must have row 0")
                else:
                    raise ConfigError("1D cell coordinates must be [index] or [0, index]")
            else:
                if len(coordinate) != 2:
                    raise ConfigError("2D cell coordinates must be [row, col]")
                row, col = coordinate
            if any(isinstance(value, bool) or not isinstance(value, int) for value in (row, col)):
                raise ConfigError(f"initial.cells.{state_name}[{index}] coordinates must be integers")
            if not infinite and (row < 0 or col < 0):
                raise ConfigError("cell coordinates must be non-negative")
            if not infinite and (col >= width or row >= height):
                raise ConfigError(f"initial.cells.{state_name}[{index}] is outside the world")
            point = (col, row)
            if point in occupied:
                raise ConfigError(f"duplicate initial cell coordinate [{row}, {col}]")
            occupied.add(point)
            result.append({"x": col, "y": row, "state": states[state_name]})
    return result


def _validate_parameter(name: str, value: Any, spec: dict[str, Any]) -> None:
    kind = spec.get("type")
    if kind == "integer":
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif kind == "number":
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    else:
        raise ConfigError(f"unsupported parameter metadata for {name!r}")
    if not valid:
        raise ConfigError(f"parameter {name!r} must be a {kind}")
    if kind == "number" and not math.isfinite(value):
        raise ConfigError(f"parameter {name!r} must be finite")
    if "minimum" in spec and value < spec["minimum"]:
        raise ConfigError(f"parameter {name!r} must be at least {spec['minimum']}")
    if "maximum" in spec and value > spec["maximum"]:
        raise ConfigError(f"parameter {name!r} must be at most {spec['maximum']}")


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{field} must be a positive integer")
    return value


def _keys(value: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ConfigError(f"unknown {field} key(s): {', '.join(sorted(map(str, unknown)))}")
