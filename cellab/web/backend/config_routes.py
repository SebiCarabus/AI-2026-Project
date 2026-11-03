"""Run-config upload/parse endpoint."""

from __future__ import annotations

import os
from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from .configs import ConfigError, parse_config_yaml
from .routes import service

router = APIRouter()
_CONFIG_CACHE_LIMIT = 8
_uploaded_configs: OrderedDict[str, dict[str, Any]] = OrderedDict()


def get_uploaded_config(config_hash: str) -> dict[str, Any] | None:
    config = _uploaded_configs.get(config_hash)
    if config is not None:
        _uploaded_configs.move_to_end(config_hash)
        return dict(config)
    return None


def _upload_limit_bytes() -> int:
    try:
        megabytes = int(os.environ.get("CELLAB_CONFIG_MAX_UPLOAD_MB", "10"))
    except ValueError as exc:
        raise RuntimeError("CELLAB_CONFIG_MAX_UPLOAD_MB must be a positive integer") from exc
    if megabytes <= 0:
        raise RuntimeError("CELLAB_CONFIG_MAX_UPLOAD_MB must be a positive integer")
    return int(megabytes * 1024 * 1024)


@router.post("/configs/parse")
async def parse_config(file: UploadFile = File(...)) -> dict[str, Any]:
    limit = _upload_limit_bytes()
    payload = await file.read(limit + 1)
    if len(payload) > limit:
        raise HTTPException(status_code=413, detail="config upload exceeds the configured size limit")
    try:
        parsed = parse_config_yaml(payload, service.preset_metadata())
        _uploaded_configs[parsed["config_hash"]] = parsed
        _uploaded_configs.move_to_end(parsed["config_hash"])
        while len(_uploaded_configs) > _CONFIG_CACHE_LIMIT:
            _uploaded_configs.popitem(last=False)
        response = {key: value for key, value in parsed.items() if key != "config_yaml"}
        response["filename"] = file.filename or "cellab-config.yaml"
        response["config"] = {key: value for key, value in response.items() if key != "filename"}
        return response
    except ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
