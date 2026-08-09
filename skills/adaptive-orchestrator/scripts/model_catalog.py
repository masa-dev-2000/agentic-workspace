"""Fail-closed resolution of logical model keys to parent-runtime model IDs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "references" / "model-catalog.json"


class ModelResolutionError(ValueError):
    pass


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelResolutionError("model catalog unavailable or invalid") from exc
    if value.get("schema") != "model_catalog_v1" or not isinstance(value.get("revision"), str):
        raise ModelResolutionError("model catalog schema invalid")
    models = value.get("models")
    if not isinstance(models, dict) or not models:
        raise ModelResolutionError("model catalog models missing")
    for key, model in models.items():
        if not isinstance(key, str) or not key or not isinstance(model, dict):
            raise ModelResolutionError("model catalog entry invalid")
        if not isinstance(model.get("runtime_model_id"), str) or not model["runtime_model_id"]:
            raise ModelResolutionError(f"model {key} has no runtime model id")
        if not isinstance(model.get("provider"), str) or not model["provider"]:
            raise ModelResolutionError(f"model {key} has no provider")
        if not isinstance(model.get("class"), str) or not model["class"]:
            raise ModelResolutionError(f"model {key} has no class")
        if model.get("enabled") is not True:
            raise ModelResolutionError(f"model {key} is disabled")
    return value


def resolve_model(model_key: str, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(model_key, str) or not model_key:
        raise ModelResolutionError("logical model key is required")
    catalog = catalog or load_catalog()
    model = catalog["models"].get(model_key)
    if not isinstance(model, dict) or model.get("enabled") is not True:
        raise ModelResolutionError(f"unknown or disabled model: {model_key}")
    return {
        "model_key": model_key,
        "runtime_model_id": model["runtime_model_id"],
        "provider": model["provider"],
        "model_class": model["class"],
        "catalog_revision": catalog["revision"],
    }
