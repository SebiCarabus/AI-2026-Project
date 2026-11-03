import pytest

try:
    from cellab.domain import TreeUniverse
    from cellab.web.backend.service import SimulationService
except ModuleNotFoundError:
    from domain import TreeUniverse
    from web.backend.service import SimulationService


def test_large_finite_sparse_world_uses_tree_and_returns_only_viewport_tile(monkeypatch):
    monkeypatch.setenv("CELLAB_TREE_THRESHOLD_MB", "1")
    service = SimulationService()
    frame = service.create({
        "preset": "game_of_life",
        "width": 10_000,
        "height": 10_000,
        "parameters": {},
        "density": 0,
        "seed": 1,
        "initial_cells": [{"x": 5, "y": 5, "state": 1}],
        "viewport": {"x": 0, "y": 0, "width": 40, "height": 30, "padding": 64},
    })

    run = service._runs[frame["id"]]
    assert isinstance(run.universe, TreeUniverse)
    assert (frame["width"], frame["height"]) == (10_000, 10_000)
    assert (len(frame["grid"][0]), len(frame["grid"])) == (104, 94)
    assert frame["counts"] == [99_999_999, 1]

    stepped = service.step(frame["id"])
    assert stepped["generation"] == 1
    assert stepped["counts"] == [100_000_000, 0]
    assert len(stepped["grid"]) < 100


def test_10k_by_1k_empty_board_from_config_uses_tree_instead_of_many_array_chunks():
    service = SimulationService()
    frame = service.create({
        "preset": "game_of_life",
        "width": 10_000,
        "height": 1_000,
        "parameters": {},
        "density": 0,
        "seed": None,
        "viewport": {"x": 0, "y": 0, "width": 40, "height": 24, "padding": 64},
    })

    assert isinstance(service._runs[frame["id"]].universe, TreeUniverse)
    assert frame["counts"] == [10_000_000, 0]
    stepped = service.step(frame["id"])
    assert stepped["generation"] == 1
    assert stepped["counts"] == [10_000_000, 0]


def test_infinite_world_supports_negative_coordinates_and_tree_snapshot_restore():
    service = SimulationService()
    frame = service.create({
        "preset": "elementary",
        "width": None,
        "height": None,
        "infinite": True,
        "parameters": {"rule": 30},
        "density": 0,
        "seed": 2,
        "initial_cells": [{"x": -3, "y": 0, "state": 1}],
        "viewport": {"x": -8, "y": 0, "width": 12, "height": 1, "padding": 8},
    })
    assert frame["infinite"] is True
    assert frame["width"] is None and frame["height"] is None
    assert frame["counts"] == [None, 1]
    assert frame["grid_origin"]["x"] == -16

    snapshot = service.export_snapshot(frame["id"])
    service.step(frame["id"])
    restored = service.restore_snapshot(frame["id"], snapshot)
    assert restored["generation"] == 0
    assert restored["counts"] == [None, 1]
    assert service._runs[frame["id"]].universe.bounds is None


def test_dense_random_world_over_tree_threshold_is_rejected_without_array_allocation(monkeypatch):
    monkeypatch.setenv("CELLAB_TREE_THRESHOLD_MB", "1")
    service = SimulationService()
    with pytest.raises(ValueError, match="sparse initial_cells"):
        service.create({
            "preset": "game_of_life",
            "width": 5_000,
            "height": 5_000,
            "parameters": {},
            "density": 0.24,
            "seed": 2,
        })
