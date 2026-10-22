"""Rancho simulation engine — a thin, auditable layer on top of CropForge.

CropForge owns the runtime: daily time-stepping, FAO-56 ET₀, radiation
interception, crop plugins, terrain and the Parquet state log.  Rancho supplies
the scenario, the seeded weather/soil generation, a cell-resolved root-zone
water and nitrogen balance (see :mod:`rancho.agronomy`), and the compact bundle
the WebGL client renders.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from cropforge import Crop, Farm, Field, Terrain
from cropforge.loaders import Soil, Weather
from cropforge.plugins.maize import StandardMaize
from cropforge.plugins.wheat import StandardWheat

from rancho.agronomy import AgronomyModel
from rancho.climate import build_soil_field, write_soil_csv, write_weather_csv
from rancho.frames import build_bundle
from rancho.scenario import ScenarioConfig

#: Crop name → bundled CropForge plugin class.
PLUGINS: dict[str, type] = {"wheat": StandardWheat, "maize": StandardMaize}

_RUN_LOCK = threading.Lock()


def run_id_for(cfg: ScenarioConfig) -> str:
    """Stable identifier for a scenario — identical configs reuse the cached run."""
    payload = cfg.model_dump_json()
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


@contextmanager
def _working_directory(path: Path) -> Iterator[None]:
    """Run the engine inside *path* so its Parquet log lands next to the scenario."""
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _apply_physics(farm: Farm, cfg: ScenarioConfig) -> None:
    """Switch on the CropForge engines the scenario asks for.

    ``water_balance`` is deliberately *not* enabled: Rancho runs its own
    cell-resolved root-zone balance instead (see :mod:`rancho.agronomy`),
    because CropForge's layered bucket collapses every column to field capacity.
    """
    physics = cfg.physics
    sloping = cfg.terrain != "flat"
    farm.use_physics(
        et0=physics.et0,
        radiation=physics.radiation,
        disease=physics.disease,
        disease_seed=cfg.seed,
        weed_pressure=physics.weeds,
        weed_seed=cfg.seed,
        slope_radiation_correction=sloping,
        terrain_wind=sloping,
    )


def _make_terrain(cfg: ScenarioConfig) -> Terrain | None:
    """Procedural terrain, or ``None`` for a flat field."""
    if cfg.terrain == "flat":
        return None
    return Terrain.procedural(
        rows=cfg.rows,
        cols=cfg.cols,
        generator=cfg.terrain,
        resolution_m=1.0,
        seed=cfg.seed,
        grade_pct=1.6,
        amplitude_m=1.8,
    )


def _build_field(
    cfg: ScenarioConfig,
    *,
    weather_path: Path,
    soil_path: Path,
    terrain: Terrain | None,
) -> Field:
    field = Field(
        name=cfg.name,
        rows=cfg.rows,
        cols=cfg.cols,
        area_ha=cfg.rows * cfg.cols / 10_000.0,
    )
    field.set_crop(Crop(species=cfg.crop, variety=cfg.variety, sowing_doy=cfg.sowing_doy))
    field.set_weather(Weather.from_csv(str(weather_path)))
    field.set_soil(
        Soil.from_csv(
            str(soil_path),
            apply="uniform" if cfg.soil_pattern == "uniform" else "spatial",
            rows=cfg.rows,
            cols=cfg.cols,
        )
    )
    field.set_planting_config(
        pattern="rows",
        row_spacing_m=cfg.row_spacing_m,
        plant_spacing_m=cfg.plant_spacing_m,
    )
    if terrain is not None:
        field.set_terrain(terrain)
    return field


def run_scenario(cfg: ScenarioConfig, work_root: Path) -> dict[str, Any]:
    """Execute one scenario and return the render-ready bundle."""
    run_id = run_id_for(cfg)
    session_dir = work_root / run_id
    inputs_dir = session_dir / "inputs"
    work_dir = session_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)

    weather_path = write_weather_csv(
        inputs_dir / "weather.csv",
        days=cfg.days + 8,
        climate=cfg.climate,
        seed=cfg.seed,
        start_doy=cfg.sowing_doy,
    )

    terrain = _make_terrain(cfg)
    elevation = (
        np.asarray(terrain.elevation_grid, dtype=np.float64)
        if terrain is not None
        else np.zeros((cfg.rows, cfg.cols), dtype=np.float64)
    )
    soil_field = build_soil_field(
        cfg.soil,
        rows=cfg.rows,
        cols=cfg.cols,
        pattern=cfg.soil_pattern,
        variation=cfg.soil_variation,
        seed=cfg.seed,
        elevation=elevation,
    )
    soil_path = write_soil_csv(inputs_dir / "soil.csv", soil_field, pattern=cfg.soil_pattern)

    farm = Farm(name=cfg.name, location=(28.6, 77.2))
    field = _build_field(cfg, weather_path=weather_path, soil_path=soil_path, terrain=terrain)
    farm.add_field(field)
    _apply_physics(farm, cfg)
    agronomy = AgronomyModel(cfg, soil_field)
    # phase 0: water + nitrogen, so the crop plugin at phase 2 reads today's state.
    farm.step(phase=0)(agronomy.step)
    field.use_plugin(PLUGINS[cfg.crop], phase=2)
    # phase 3: canopy, so the leaf area the row records matches the water supply.
    farm.step(phase=3)(agronomy.adjust_canopy)

    started = time.perf_counter()
    with _RUN_LOCK, _working_directory(work_dir):
        farm.run(days=cfg.days)
    elapsed = time.perf_counter() - started

    sessions = sorted((work_dir / "cropforge_output").glob("*"))
    if not sessions:
        raise RuntimeError("CropForge produced no state log for this run.")

    try:
        yield_summary: dict[str, Any] | None = farm.yield_summary()
    except Exception:  # a missing yield summary must not sink the whole run
        yield_summary = None

    return build_bundle(
        cfg=cfg,
        log_dir=sessions[-1],
        elevation=elevation,
        yield_summary=yield_summary,
        elapsed_s=elapsed,
        run_id=run_id,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
