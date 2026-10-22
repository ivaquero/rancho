"""HTTP surface of Rancho: scenarios in, render-ready bundles out."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from rancho import __version__
from rancho.engine import run_id_for, run_scenario
from rancho.scenario import DEFAULT_PRESET, PRESETS, ScenarioConfig
from rancho.store import RunStore

logger = logging.getLogger("rancho.api")

_PLACEHOLDER = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>Rancho</title>
<style>body{font:15px/1.7 ui-sans-serif,system-ui;margin:12vh auto;max-width:36rem;color:#22312b}
code{background:#eef1ec;padding:.15em .4em;border-radius:4px}</style></head>
<body><h1>Rancho 服务已启动</h1>
<p>前端产物尚未构建。先执行：</p>
<pre><code>cd frontend &amp;&amp; pnpm install &amp;&amp; pnpm build</code></pre>
<p>然后刷新本页；接口文档见 <a href="/api/docs">/api/docs</a>。</p></body></html>"""


class RunRequest(BaseModel):
    """Either a named preset or an explicit scenario config."""

    preset: str | None = None
    config: ScenarioConfig | None = None


def _resolve(request: RunRequest) -> ScenarioConfig:
    if request.config is not None:
        return request.config
    preset_id = request.preset or DEFAULT_PRESET
    preset = PRESETS.get(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail=f"未知预设：{preset_id}")
    return ScenarioConfig.model_validate(preset["config"])


def create_app(work_root: Path, dist_dir: Path | None = None) -> FastAPI:
    """Build the FastAPI app serving the API and (optionally) the built client."""
    store = RunStore(work_root / "bundles")
    app = FastAPI(
        title="Rancho · 农田作物仿真台",
        version=__version__,
        description="基于 CropForge 的矩形网格农田仿真与 Three.js 可视化服务。",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "engine": "cropforge"}

    @app.get("/api/presets")
    def list_presets() -> list[dict[str, Any]]:
        return [
            {
                "id": key,
                "label": preset["label"],
                "description": preset["description"],
                "config": preset["config"],
            }
            for key, preset in PRESETS.items()
        ]

    @app.get("/api/runs")
    def list_runs() -> list[dict[str, Any]]:
        return store.list()

    @app.post("/api/runs")
    def create_run(request: RunRequest) -> dict[str, Any]:
        config = _resolve(request)
        run_id = run_id_for(config)
        cached = store.get(run_id)
        if cached is not None:
            logger.info("cache hit %s", run_id)
            return cached

        logger.info(
            "simulating %s (%dx%d, %d days, %s)",
            run_id,
            config.rows,
            config.cols,
            config.days,
            config.crop,
        )
        bundle = run_scenario(config, work_root)
        store.put(bundle)
        return bundle

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        bundle = store.get(run_id)
        if bundle is None:
            raise HTTPException(status_code=404, detail=f"未知运行：{run_id}")
        return bundle

    @app.delete("/api/runs/{run_id}")
    def delete_run(run_id: str) -> dict[str, bool]:
        return {"deleted": store.delete(run_id)}

    if dist_dir is not None and (dist_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="client")
    else:

        @app.get("/", response_class=HTMLResponse)
        def placeholder() -> str:
            return _PLACEHOLDER

    return app
