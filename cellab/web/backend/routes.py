"""HTTP routes and request validation for the frontend simulation API."""

import asyncio
import math
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .service import SimulationService

router = APIRouter()
service = SimulationService()


class Viewport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    padding: int = Field(ge=0)


class InitialCell(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    x: int
    y: int = 0
    state: int = Field(ge=0)


class SimulationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: str
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    infinite: bool = False
    parameters: dict[str, Any] = Field(default_factory=dict)
    density: float = Field(ge=0.0, le=1.0)
    seed: int | None = Field(default=None, ge=0)
    viewport: Viewport | None = None
    initial_cells: list[InitialCell] = Field(default_factory=list)
    config_yaml: str | None = None
    config_hash: str | None = None
    name: str | None = None

    @model_validator(mode="after")
    def validate_world_dimensions(self):
        if self.infinite:
            if self.width is not None or self.height is not None:
                raise ValueError("infinite worlds must omit width and height")
            if self.density != 0:
                raise ValueError("infinite worlds require density 0")
        elif self.width is None or self.height is None:
            raise ValueError("finite worlds require positive width and height")
        return self


class GridEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grid: list[list[int]]


@router.get("/presets")
def get_presets() -> list[dict[str, Any]]:
    return service.preset_metadata()


@router.post("/simulations")
def create_simulation(config: SimulationConfig) -> dict[str, Any]:
    try:
        payload = config.model_dump()
        if payload.get("config_hash") and not payload.get("config_yaml"):
            from .config_routes import get_uploaded_config

            uploaded = get_uploaded_config(payload["config_hash"])
            if uploaded is None:
                raise ValueError("uploaded config expired from the server cache; upload it again")
            payload.update({key: uploaded[key] for key in ("config_yaml", "config_hash", "name", "initial_cells")})
        return service.create(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/simulations/{simulation_id}")
def get_simulation(simulation_id: str) -> dict[str, Any]:
    try:
        return service.frame(simulation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Simulation not found") from exc


@router.post("/simulations/{simulation_id}/step")
def step_simulation(simulation_id: str) -> dict[str, Any]:
    try:
        return service.step(simulation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Simulation not found") from exc


@router.put("/simulations/{simulation_id}/grid")
def edit_grid(simulation_id: str, edit: GridEdit) -> dict[str, Any]:
    try:
        return service.replace_grid(simulation_id, edit.grid)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Simulation not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/simulations/{simulation_id}/reset")
def reset_simulation(simulation_id: str, config: SimulationConfig) -> dict[str, Any]:
    try:
        payload = config.model_dump()
        if payload.get("config_hash") and not payload.get("config_yaml"):
            from .config_routes import get_uploaded_config

            uploaded = get_uploaded_config(payload["config_hash"])
            if uploaded is None:
                raise ValueError("uploaded config expired from the server cache; upload it again")
            payload.update({key: uploaded[key] for key in ("config_yaml", "config_hash", "name", "initial_cells")})
        return service.reset(simulation_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Simulation not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.websocket("/simulations/{simulation_id}/stream")
async def stream_simulation(websocket: WebSocket, simulation_id: str) -> None:
    await websocket.accept()
    try:
        service.frame(simulation_id)
    except KeyError:
        await websocket.close(code=4404, reason="Simulation not found")
        return

    loop = asyncio.get_running_loop()
    receiving = asyncio.create_task(websocket.receive_json())
    running = False
    interval = 1.0
    next_step = 0.0
    try:
        while True:
            if running:
                timeout = max(0.0, next_step - loop.time())
                completed, _ = await asyncio.wait({receiving}, timeout=timeout)
                if not completed:
                    await websocket.send_json(service.step(simulation_id))
                    next_step = loop.time() + interval
                    continue
                message = receiving.result()
            else:
                message = await receiving
            receiving = asyncio.create_task(websocket.receive_json())

            if not isinstance(message, dict):
                await websocket.send_json({"type": "error", "detail": "message must be an object"})
                continue
            command = message.get("type")
            try:
                if command == "viewport":
                    frame = service.set_viewport(simulation_id, message)
                    await websocket.send_json(frame)
                elif command == "start":
                    speed = message.get("speed")
                    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
                        raise ValueError("speed must be a positive finite number")
                    if not math.isfinite(speed) or speed <= 0:
                        raise ValueError("speed must be a positive finite number")
                    interval = 1.0 / speed
                    next_step = loop.time() + interval
                    running = True
                elif command == "pause":
                    running = False
                elif command == "step":
                    await websocket.send_json(service.step(simulation_id))
                elif command == "paint":
                    cells = message.get("cells")
                    if not isinstance(cells, list):
                        raise ValueError("cells must be an array")
                    await websocket.send_json(service.paint_cells(simulation_id, cells))
                elif command == "clear":
                    await websocket.send_json(service.clear(simulation_id))
                else:
                    raise ValueError("type must be viewport, start, pause, step, paint, or clear")
            except ValueError as exc:
                await websocket.send_json({"type": "error", "detail": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        if not receiving.done():
            receiving.cancel()
