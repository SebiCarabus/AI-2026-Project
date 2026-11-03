import asyncio
import time

import httpx
from fastapi.testclient import TestClient

try:
    from cellab.web.backend.main import app
except ModuleNotFoundError:
    from web.backend.main import app


def request(method, path, **kwargs):
    async def send():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_preset_metadata_describes_all_four_presets():
    response = request("GET", "/api/presets")

    assert response.status_code == 200
    presets = {item["id"]: item for item in response.json()}
    assert set(presets) == {"game_of_life", "elementary", "cyclic", "forest_fire"}
    assert presets["elementary"]["dimensionality"] == 1
    assert presets["elementary"]["parameters"]["rule"]["default"] == 30
    assert all(state["color"].startswith("#") for item in presets.values() for state in item["states"])


def test_vite_origin_is_allowed_by_cors():
    response = request(
        "OPTIONS",
        "/api/presets",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_elementary_uses_width_as_1d_length_and_steps_one_generation():
    created = request(
        "POST",
        "/api/simulations",
        json={"preset": "elementary", "width": 5, "height": 9, "parameters": {"rule": 30}, "density": 0, "seed": 4},
    )
    assert created.status_code == 200
    initial = created.json()
    assert initial["one_dimensional"] is True
    assert (initial["width"], initial["height"], initial["generation"]) == (5, 1, 0)
    assert initial["grid"] == [[0, 0, 0, 0, 0]]

    stepped = request("POST", f"/api/simulations/{initial['id']}/step").json()
    assert stepped["generation"] == 1
    assert stepped["grid"] == [[0, 0, 0, 0, 0]]
    assert request("GET", f"/api/simulations/{initial['id']}").json() == stepped


def test_grid_edit_keeps_generation_and_reset_reinitializes_the_run():
    config = {"preset": "game_of_life", "width": 3, "height": 2, "parameters": {}, "density": 0, "seed": 7}
    created = request("POST", "/api/simulations", json=config).json()

    edited = request(
        "PUT",
        f"/api/simulations/{created['id']}/grid",
        json={"grid": [[0, 1, 0], [0, 1, 0]]},
    )
    assert edited.status_code == 200
    assert edited.json()["generation"] == 0
    assert edited.json()["grid"] == [[0, 1, 0], [0, 1, 0]]

    stepped = request("POST", f"/api/simulations/{created['id']}/step").json()
    assert stepped["generation"] == 1
    reset = request("POST", f"/api/simulations/{created['id']}/reset", json=config)
    assert reset.status_code == 200
    assert reset.json()["id"] == created["id"]
    assert reset.json()["generation"] == 0
    assert reset.json()["grid"] == [[0, 0, 0], [0, 0, 0]]


def test_seed_reproduces_multistate_initial_grid_and_bad_grids_are_rejected():
    config = {"preset": "cyclic", "width": 6, "height": 4, "parameters": {}, "density": 0.7, "seed": 21}
    first = request("POST", "/api/simulations", json=config).json()
    second = request("POST", "/api/simulations", json=config).json()
    assert first["grid"] == second["grid"]

    invalid = request("PUT", f"/api/simulations/{first['id']}/grid", json={"grid": [[0]]})
    assert invalid.status_code == 422
    missing = request("GET", "/api/simulations/not-a-run")
    assert missing.status_code == 404


def test_forest_fire_runs_stochastic_steps_reproducibly():
    config = {
        "preset": "forest_fire",
        "width": 8,
        "height": 7,
        "parameters": {"growth": 0.2, "lightning": 0.03},
        "density": 0.8,
        "seed": 12,
    }
    first = request("POST", "/api/simulations", json=config).json()
    second = request("POST", "/api/simulations", json=config).json()
    first_step = request("POST", f"/api/simulations/{first['id']}/step").json()
    second_step = request("POST", f"/api/simulations/{second['id']}/step").json()

    assert first["states"] == [
        {"name": "empty", "color": "#ffffff"},
        {"name": "tree", "color": "#228b22"},
        {"name": "burning", "color": "#ff4500"},
    ]
    assert first_step["generation"] == 1
    assert first_step["grid"] == second_step["grid"]


def test_viewport_frames_are_cropped_and_include_full_world_counts():
    config = {
        "preset": "game_of_life",
        "width": 6,
        "height": 4,
        "parameters": {},
        "density": 0,
        "seed": 1,
        "viewport": {"x": 2, "y": 1, "width": 2, "height": 2, "padding": 1},
    }
    created = request("POST", "/api/simulations", json=config).json()
    edited = request(
        "PUT",
        f"/api/simulations/{created['id']}/grid",
        json={"grid": [[0, 1, 0, 0, 0, 0], [1, 1, 1, 0, 0, 0], [0, 0, 1, 1, 0, 0], [0, 0, 0, 0, 0, 0]]},
    ).json()

    assert edited["view"] == {"x": 2, "y": 1, "width": 2, "height": 2}
    assert edited["grid_origin"] == {"x": 1, "y": 0}
    assert edited["grid"] == [[1, 0, 0, 0], [1, 1, 0, 0], [0, 1, 1, 0], [0, 0, 0, 0]]
    assert edited["counts"] == [18, 6]


def test_elementary_viewport_is_one_row_with_one_dimensional_view_metadata():
    response = request(
        "POST",
        "/api/simulations",
        json={
            "preset": "elementary",
            "width": 10,
            "height": 6,
            "parameters": {},
            "density": 0,
            "seed": 1,
            "viewport": {"x": 3, "y": 4, "width": 2, "height": 5, "padding": 1},
        },
    )

    frame = response.json()
    assert frame["view"] == {"x": 3, "y": 0, "width": 2, "height": 1}
    assert frame["grid_origin"] == {"x": 2, "y": 0}
    assert frame["grid"] == [[0, 0, 0, 0]]


def test_websocket_viewport_step_paint_and_timed_start_pause():
    config = {
        "preset": "game_of_life",
        "width": 5,
        "height": 5,
        "parameters": {},
        "density": 0,
        "seed": 1,
    }
    created = request("POST", "/api/simulations", json=config).json()
    client = TestClient(app)

    with client.websocket_connect(f"/api/simulations/{created['id']}/stream") as websocket:
        websocket.send_json({"type": "viewport", "x": 1, "y": 1, "width": 2, "height": 2, "padding": 0})
        viewport = websocket.receive_json()
        assert viewport["view"] == {"x": 1, "y": 1, "width": 2, "height": 2}
        assert len(viewport["grid"]) == 2
        assert len(viewport["grid"][0]) == 2

        websocket.send_json({"type": "step"})
        stepped = websocket.receive_json()
        assert stepped["generation"] == 1

        websocket.send_json({"type": "paint", "cells": [{"x": 1, "y": 1, "state": 1}]})
        painted = websocket.receive_json()
        assert painted["grid"] == [[1, 0], [0, 0]]
        assert painted["counts"] == [24, 1]

        websocket.send_json({"type": "start", "speed": 2})
        started = websocket.receive_json()
        assert started["generation"] == 2
        websocket.send_json({"type": "pause"})
        time.sleep(0.55)
        paused = request("GET", f"/api/simulations/{created['id']}").json()
        assert paused["generation"] == 2

        websocket.send_json({"type": "clear"})
        cleared = websocket.receive_json()
        assert cleared["generation"] == 2
        assert cleared["counts"] == [25, 0]


def test_chunked_steps_match_full_domain_steps_across_tile_boundaries():
    import numpy as np

    try:
        from cellab.domain import Grid
        from cellab.domain.presets import elementary, game_of_life
        from cellab.web.backend.service import SimulationService
    except ModuleNotFoundError:
        from domain import Grid
        from domain.presets import elementary, game_of_life
        from web.backend.service import SimulationService

    life_grid = Grid(np.array([[(row * 3 + col) % 2 for col in range(11)] for row in range(8)], dtype=np.uint8))
    line_grid = Grid(np.array([index % 2 for index in range(17)], dtype=np.uint8))
    cases = [(game_of_life(), life_grid), (elementary(30), line_grid)]

    for automaton, grid in cases:
        expected = automaton.step(grid, rng=np.random.default_rng(8))
        actual, counts = SimulationService._step_chunked(
            automaton,
            grid,
            np.random.default_rng(8),
            chunk_size=3,
        )
        assert actual == expected
        assert counts == [int(count) for count in expected.counts(automaton.num_states)]
