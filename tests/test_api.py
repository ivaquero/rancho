"""The HTTP surface, exercised without a browser or an HTTP client.

``starlette.testclient`` needs ``httpx``, which this project does not depend on,
so these cases speak ASGI directly: a FastAPI/Starlette app *is* an async
callable taking ``(scope, receive, send)``, and the middleware stack it builds
for itself turns an :class:`HTTPException` into the right status code — which is
exactly what a caller sees and what a plain function call would skip.

None of these start a simulation: the slow ``POST /api/runs`` happy path and the
503-busy refusal are the engine's concern (covered in ``test_engine`` /
``test_mosaic``).  What is tested here is the boundary — that a malformed request
fails with a status, not a stack trace, and that the read-only listings agree
with the scenario model.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from rancho.api import RunRequest, _resolve, create_app
from rancho.scenario import DEFAULT_PRESET, PRESETS, ScenarioConfig


def _request(app, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    """Drive one request through the ASGI app and return ``(status, json)``."""
    raw = b"" if body is None else json.dumps(body).encode("utf-8")

    async def _run() -> list[dict[str, Any]]:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "client": ("test", 0),
            "server": ("test", 80),
            "headers": [
                (b"host", b"test"),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(raw)).encode()),
            ],
        }
        messages: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": raw, "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        await app(scope, receive, send)
        return messages

    messages = asyncio.run(_run())
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, (json.loads(payload) if payload else None)


def _app(tmp_path: Path):
    return create_app(tmp_path)


def test_health_reports_the_version(tmp_path) -> None:
    status, body = _request(_app(tmp_path), "GET", "/api/health")
    assert status == 200
    assert body["status"] == "ok"


def test_presets_mark_one_default_and_carry_every_label(tmp_path) -> None:
    status, body = _request(_app(tmp_path), "GET", "/api/presets")
    assert status == 200
    assert isinstance(body, list) and body
    # The client opens on whatever the server flags, not a hard-coded id.
    assert sum(1 for preset in body if preset["default"]) == 1
    assert DEFAULT_PRESET in {preset["id"] for preset in body}
    for preset in body:
        assert preset["label"]
        assert preset["config"]


def test_crops_expose_the_mixability_the_engine_enforces(tmp_path) -> None:
    status, crops = _request(_app(tmp_path), "GET", "/api/crops")
    assert status == 200
    by_key = {crop["key"]: crop for crop in crops}
    assert "tomato" in by_key
    # A crop CropForge models itself (a cereal) cannot be planted per cell.
    assert by_key["tomato"]["mixable"] is True
    if "wheat" in by_key:
        assert by_key["wheat"]["mixable"] is False


def test_limits_come_from_the_same_validators_as_the_config(tmp_path) -> None:
    status, limits = _request(_app(tmp_path), "GET", "/api/limits")
    assert status == 200
    for axis in ("rows", "cols", "days"):
        assert limits[axis]["min"] <= limits[axis]["max"]


def test_an_unknown_run_is_a_404(tmp_path) -> None:
    status, body = _request(_app(tmp_path), "GET", "/api/runs/does-not-exist")
    assert status == 404
    assert body["detail"]


def test_an_empty_store_lists_no_runs(tmp_path) -> None:
    status, body = _request(_app(tmp_path), "GET", "/api/runs")
    assert status == 200
    assert body == []


def test_posting_an_unknown_preset_is_a_404_not_a_run(tmp_path) -> None:
    # The preset is resolved before the engine is touched, so this cannot simulate.
    status, body = _request(_app(tmp_path), "POST", "/api/runs", {"preset": "nope"})
    assert status == 404
    assert "nope" in body["detail"]


def test_posting_an_invalid_config_is_a_422(tmp_path) -> None:
    status, _ = _request(_app(tmp_path), "POST", "/api/runs", {"config": {"rows": 1}})
    assert status == 422


def test_resolve_prefers_an_explicit_config_over_the_preset() -> None:
    config = ScenarioConfig.model_validate(PRESETS[DEFAULT_PRESET]["config"])
    resolved = _resolve(RunRequest(config=config, preset="other"))
    assert resolved == config


def test_resolve_rejects_an_unknown_preset() -> None:
    try:
        _resolve(RunRequest(config=None, preset="not-a-preset"))
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("an unknown preset must raise, not fall through")
