"""HTTP endpoints for saved run snapshots and config downloads."""

from __future__ import annotations

from typing import Any

import psycopg
import yaml
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from .routes import service
from .snapshots import SnapshotNotFound, SnapshotStore

router = APIRouter()
snapshot_store = SnapshotStore()


@router.get("/simulations/{simulation_id}/config")
def download_config(simulation_id: str) -> Response:
    try:
        config_yaml, config_hash = service.export_config(simulation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Simulation not found") from exc
    return Response(
        content=config_yaml,
        media_type="text/yaml",
        headers={
            "Content-Disposition": 'attachment; filename="config.yaml"',
            "X-Config-Hash": config_hash,
        },
    )


@router.post("/simulations/{simulation_id}/snapshots")
def create_snapshot(simulation_id: str) -> dict[str, Any]:
    try:
        snapshot = service.export_snapshot(simulation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Simulation not found") from exc
    name = _display_name(snapshot)
    try:
        return snapshot_store.save(snapshot, name)
    except (psycopg.Error, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Snapshot storage is unavailable") from exc


@router.get("/snapshots")
def list_snapshots(config_hash: str | None = Query(default=None, min_length=1)) -> list[dict[str, Any]]:
    try:
        return snapshot_store.list(config_hash)
    except (psycopg.Error, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Snapshot storage is unavailable") from exc


@router.post("/simulations/{simulation_id}/snapshots/{snapshot_id}/restore")
def restore_snapshot(simulation_id: str, snapshot_id: int) -> dict[str, Any]:
    try:
        snapshot = snapshot_store.get(snapshot_id)
    except SnapshotNotFound as exc:
        raise HTTPException(status_code=404, detail="Snapshot not found") from exc
    except (psycopg.Error, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Snapshot storage is unavailable") from exc
    try:
        return service.restore_snapshot(simulation_id, snapshot)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Simulation not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _display_name(snapshot: dict[str, Any]) -> str:
    try:
        config = yaml.safe_load(snapshot["config_yaml"])
        name = config.get("name") if isinstance(config, dict) else None
    except (KeyError, yaml.YAMLError):
        name = None
    if isinstance(name, str) and name.strip():
        return name
    preset = snapshot.get("preset", "CelLab")
    metadata = {entry["id"]: entry for entry in service.preset_metadata()}
    return metadata.get(preset, {}).get("display_name", str(preset))
