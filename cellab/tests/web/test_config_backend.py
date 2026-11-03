import asyncio
import hashlib

import httpx
import pytest

try:
    from cellab.web.backend.configs import ConfigError, parse_config_yaml
    from cellab.web.backend.main import app
    from cellab.web.backend.routes import service
except ModuleNotFoundError:
    from web.backend.configs import ConfigError, parse_config_yaml
    from web.backend.main import app
    from web.backend.routes import service


PRESETS = service.preset_metadata()


def api_request(method, path, **kwargs):
    async def send():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_config_yaml_is_canonicalized_and_hashed_as_uploaded():
    source = b"""version: 1
name: My Life board
preset: game_of_life
world:
  width: 100
  height: 80
  infinite: false
parameters: {}
initial:
  density: 0.2
  seed: 12
  cells:
    alive: [[10, 12], [10, 13]]
"""

    result = parse_config_yaml(source, PRESETS)

    assert result == {
        "preset": "game_of_life",
        "width": 100,
        "height": 80,
        "infinite": False,
        "parameters": {},
        "density": 0.2,
        "seed": 12,
        "initial_cells": [
            {"x": 12, "y": 10, "state": 1},
            {"x": 13, "y": 10, "state": 1},
        ],
        "config_yaml": source.decode(),
        "config_hash": hashlib.sha256(source).hexdigest(),
        "name": "My Life board",
    }


def test_config_defaults_parameters_and_accepts_both_elementary_coordinate_forms():
    result = parse_config_yaml(
        b"version: 1\nname: Line\npreset: elementary\nworld: {width: 8}\n"
        b"initial: {cells: {alive: [[3], [0, 6]]}}\n",
        PRESETS,
    )

    assert result["parameters"] == {"rule": 30}
    assert result["height"] == 1
    assert result["initial_cells"] == [
        {"x": 3, "y": 0, "state": 1},
        {"x": 6, "y": 0, "state": 1},
    ]


@pytest.mark.parametrize(
    "source, message",
    [
        (b"version: 1\nname: bad\npreset: game_of_life\nworld: {width: 2, height: 2, extra: 1}\ninitial: {}", "unknown world key"),
        (b"version: 1\nname: bad\npreset: game_of_life\nworld: {infinite: true}\ninitial: {density: 0.1, cells: {}}", "density must be 0"),
        (b"version: 1\nname: bad\npreset: game_of_life\nworld: {width: 2, height: 2}\ninitial: {cells: {alive: [[2, 0]]}}", "outside the world"),
        (b"version: 1\nname: bad\npreset: game_of_life\nworld: {width: 2, height: 2}\ninitial: {cells: {ghost: [[0, 0]]}}", "unknown initial.cells state"),
    ],
)
def test_invalid_configs_are_rejected(source, message):
    with pytest.raises(ConfigError, match=message):
        parse_config_yaml(source, PRESETS)


def test_infinite_world_requires_explicit_finite_cells_and_maps_1d_coordinates():
    source = b"""version: 1
name: Infinite line
preset: elementary
world: {infinite: true}
initial: {density: 0, cells: {alive: [[9]]}}
"""
    parsed = parse_config_yaml(source, PRESETS)
    assert parsed["width"] is None and parsed["height"] is None
    assert parsed["initial_cells"] == [{"x": 9, "y": 0, "state": 1}]

    negative = source.replace(b"[[9]]", b"[[-9]]")
    assert parse_config_yaml(negative, PRESETS)["initial_cells"] == [{"x": -9, "y": 0, "state": 1}]

    with pytest.raises(ConfigError, match="required for an infinite world"):
        parse_config_yaml(source.replace(b", cells: {alive: [[9]]}", b""), PRESETS)


def test_parse_endpoint_accepts_multipart_and_enforces_upload_limit(monkeypatch):
    valid = b"version: 1\nname: API test\npreset: game_of_life\nworld: {width: 2, height: 2}\ninitial: {}\n"
    parsed = api_request("POST", "/api/configs/parse", files={"file": ("run.yaml", valid)})
    assert parsed.status_code == 200
    assert parsed.json()["config_hash"] == hashlib.sha256(valid).hexdigest()
    assert parsed.json()["name"] == "API test"
    assert parsed.json()["config"]["preset"] == "game_of_life"
    assert "config_yaml" not in parsed.json()

    uploaded_config = parsed.json()["config"]
    created = api_request(
        "POST",
        "/api/simulations",
        json={**uploaded_config, "viewport": {"x": 0, "y": 0, "width": 2, "height": 2, "padding": 1}},
    )
    assert created.status_code == 200
    assert created.json()["config_hash"] == parsed.json()["config_hash"]

    monkeypatch.setenv("CELLAB_CONFIG_MAX_UPLOAD_MB", "1")
    oversized = api_request(
        "POST", "/api/configs/parse", files={"file": ("big.yaml", b"x" * (1024 * 1024 + 1))}
    )
    assert oversized.status_code == 413


def test_snapshot_routes_delegate_state_operations_to_service_and_repository(monkeypatch):
    try:
        from cellab.web.backend import snapshot_routes
    except ModuleNotFoundError:
        from web.backend import snapshot_routes

    config_yaml = "version: 1\nname: Saved board\npreset: game_of_life\n"
    payload = {
        "config_hash": "hash-123",
        "config_yaml": config_yaml,
        "preset": "game_of_life",
        "width": 3,
        "height": 2,
        "counts": [5, 1],
        "generation": 4,
        "state_format": "dense-u8-v1",
        "state_blob": b"\x00\x01",
    }
    metadata = {
        "id": 19,
        "config_hash": "hash-123",
        "preset": "game_of_life",
        "display_name": "Saved board",
        "width": 3,
        "height": 2,
        "counts": [5, 1],
        "generation": 4,
        "created_at": "2026-01-02T00:00:00+00:00",
    }

    class FakeStore:
        def save(self, snapshot, display_name):
            assert snapshot == payload
            assert display_name == "Saved board"
            return metadata

        def list(self, config_hash):
            assert config_hash == "hash-123"
            return [metadata]

        def get(self, snapshot_id):
            assert snapshot_id == 19
            return payload

    monkeypatch.setattr(service, "export_config", lambda _sim_id: (config_yaml, "hash-123"), raising=False)
    monkeypatch.setattr(service, "export_snapshot", lambda _sim_id: payload, raising=False)
    monkeypatch.setattr(service, "restore_snapshot", lambda _sim_id, snapshot: {"generation": snapshot["generation"]}, raising=False)
    monkeypatch.setattr(snapshot_routes, "snapshot_store", FakeStore())

    downloaded = api_request("GET", "/api/simulations/run-1/config")
    created = api_request("POST", "/api/simulations/run-1/snapshots")
    listed = api_request("GET", "/api/snapshots?config_hash=hash-123")
    restored = api_request("POST", "/api/simulations/run-1/snapshots/19/restore")

    assert downloaded.status_code == 200
    assert downloaded.text == config_yaml
    assert downloaded.headers["content-type"].startswith("text/yaml")
    assert downloaded.headers["x-config-hash"] == "hash-123"
    assert "attachment" in downloaded.headers["content-disposition"]
    assert created.json() == metadata
    assert listed.json() == [metadata]
    assert restored.json() == {"generation": 4}
