"""HTTP surface of Rancho: scenarios in, render-ready bundles out."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from rancho import __version__
from rancho.crops import TEMPLATES
from rancho.engine import RunBusyError, run_id_for, run_scenario
from rancho.scenario import (
    DEFAULT_PRESET,
    MOSAIC_KEYS,
    PRESETS,
    ScenarioConfig,
    horizon_days,
    season_days,
)
from rancho.store import RunStore
from rancho.weather import WeatherError, fetch_weather

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


def _default_scenario() -> ScenarioConfig:
    """The scenario the UI opens on — what a slider falls back to."""
    return ScenarioConfig.model_validate(PRESETS[DEFAULT_PRESET]["config"])


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
                "label_en": preset["label_en"],
                "label_es": preset["label_es"],
                "description": preset["description"],
                "description_en": preset["description_en"],
                "description_es": preset["description_es"],
                "config": preset["config"],
                # The client opens on this one, so the server decides rather
                # than the frontend duplicating DEFAULT_PRESET.
                "default": key == DEFAULT_PRESET,
            }
            for key, preset in PRESETS.items()
        ]

    @app.get("/api/crops")
    def list_crops() -> list[dict[str, Any]]:
        """Every selectable crop: what it is called and whether a cell can hold it."""
        return [
            {
                "key": key,
                "label": template.label,
                "label_en": template.label_en,
                "label_es": template.label_es,
                "species": template.species,
                # Crops whose phenology CropForge owns cannot be planted per
                # cell — see :data:`rancho.scenario.MOSAIC_KEYS`.
                "mixable": key in MOSAIC_KEYS,
                "season_gdd": template.season_gdd,
                # Days this crop needs to mature — the client sets the horizon
                # to the largest of these over the cells it planted.
                "season_days": template.season.days_to_maturity,
                "root_zone_mm": template.water.root_zone_mm,
                "has_market": template.market is not None,
            }
            for key, template in TEMPLATES.items()
        ]

    @app.get("/api/limits")
    def list_limits() -> dict[str, Any]:
        """The field sizes the engine accepts — what the sliders run between."""

        def bounds(name: str) -> dict[str, Any]:
            low: float | None = None
            high: float | None = None
            for meta in ScenarioConfig.model_fields[name].metadata:
                low = getattr(meta, "ge", low)
                high = getattr(meta, "le", high)
            default = ScenarioConfig.model_fields[name].default
            if default is None:
                # ``days`` has no fixed default: it means "until the crop is
                # ripe", which is not a number a slider can show.  Report what
                # the default field's crop asks for instead.
                default = season_days(_default_scenario())
            return {"min": low, "max": high, "default": default}

        return {name: bounds(name) for name in ("rows", "cols", "days")}

    @app.get("/api/weather")
    def get_weather(
        lat: float,
        lon: float,
        start: date,
        days: int = Query(default=30, ge=1, le=400),
        protected: bool = False,
        transmission: float = Query(default=1.0, gt=0.2, le=1.0),
    ) -> dict[str, Any]:
        """Observed daily weather for a coordinate — what a run would consume."""
        try:
            series = fetch_weather(
                lat,
                lon,
                start,
                days,
                cache_dir=work_root / "weather",
                protected=protected,
                cover_transmission=transmission,
            )
        except WeatherError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        return {
            "meta": series.meta(),
            "days": [
                {
                    "date": day.day.isoformat(),
                    "tmax_c": round(day.tmax_c, 1),
                    "tmin_c": round(day.tmin_c, 1),
                    "radiation_mj": round(day.radiation_mj, 1),
                    "rainfall_mm": round(day.rainfall_mm, 1),
                    "humidity_pct": round(day.humidity_pct),
                    "wind_ms": round(day.wind_ms, 1),
                }
                for day in series.days
            ],
        }

    @app.get("/api/runs")
    def list_runs() -> list[dict[str, Any]]:
        return store.summaries()

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
            horizon_days(config),
            config.crop,
        )
        try:
            bundle = run_scenario(config, work_root, wait=False)
        except RunBusyError as exc:
            # Refusing beats queueing: the caller learns in milliseconds that
            # the engine is busy instead of waiting out someone else's run.
            raise HTTPException(status_code=503, detail=str(exc)) from exc
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
