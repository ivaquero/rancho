"""Convert a CropForge Parquet log into a compact, browser-ready bundle.

The state log is authoritative but verbose (one row per plant per day).  The
renderer only needs one value per grid cell and day, so this module pivots the
log into dense ``(day, cell)`` arrays and quantises each channel to ``uint8``
before base64-encoding it.  A 150-day, 26x34 run ships in roughly 180 kB.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from rancho.crops import STAGE_ORDER
from rancho.scenario import (
    CROP_PROFILES,
    STAGE_LABELS,
    ScenarioConfig,
    crop_grid,
    crop_indices,
    crop_keys,
)


@dataclass(frozen=True, slots=True)
class ChannelSpec:
    """Static description of one per-cell variable."""

    name: str
    label: str
    unit: str
    kind: str = "continuous"


CHANNELS: Final[tuple[ChannelSpec, ...]] = (
    ChannelSpec("lai", "叶面积指数", "m²/m²"),
    ChannelSpec("biomass", "单株生物量", "g"),
    ChannelSpec("height", "株高", "cm"),
    ChannelSpec("stress", "胁迫指数", "0–1"),
    ChannelSpec("moisture", "表层含水率", "%"),
    ChannelSpec("nitrogen", "表层氮素", "kg/ha"),
    ChannelSpec("disease", "病害严重度", "0–1"),
    ChannelSpec("stage", "生育阶段", "阶段", kind="stage"),
)

_QUANT_MAX: Final = 255.0


def _read_table(log_dir: Path, name: str) -> pd.DataFrame:
    """Read one Parquet dataset, normalising the ``day`` partition column.

    CropForge partitions by ``day``, so pandas hands it back as a *categorical*
    ordered by first appearance (1, 10, 11, …). Sorting that column directly
    scrambles the series, so the partition key is cast to a plain integer first.
    """
    frame = pd.read_parquet(log_dir / name)
    if "day" in frame.columns:
        frame["day"] = frame["day"].astype("int64")
        frame = frame.sort_values("day", kind="stable")
    return frame


def _pivot(
    frame: pd.DataFrame,
    column: str,
    *,
    days: int,
    rows: int,
    cols: int,
) -> np.ndarray:
    """Scatter a long-form (day, row, col) column into a dense ``(days, cells)`` grid."""
    grid = np.full((days, rows * cols), np.nan, dtype=np.float64)
    if frame.empty:
        return grid

    day_index = frame["day"].to_numpy(dtype=np.int64) - 1
    cell_index = frame["row"].to_numpy(dtype=np.int64) * cols + frame["col"].to_numpy(
        dtype=np.int64
    )
    values = frame[column].to_numpy(dtype=np.float64)
    valid = (day_index >= 0) & (day_index < days) & (cell_index >= 0) & (cell_index < rows * cols)
    grid[day_index[valid], cell_index[valid]] = values[valid]
    return grid


def _fill_missing(grid: np.ndarray) -> np.ndarray:
    """Replace holes left by cells that had no record with the run-wide mean."""
    if not np.isnan(grid).any():
        return grid
    mean = float(np.nanmean(grid)) if not np.isnan(grid).all() else 0.0
    return np.nan_to_num(grid, nan=mean)


def _encode(values: np.ndarray, low: float, high: float) -> str:
    """Quantise a float slice to uint8 and return it base64-encoded."""
    span = high - low
    if span <= 1e-9:
        quantised = np.zeros(values.shape, dtype=np.uint8)
    else:
        scaled = (values - low) / span * _QUANT_MAX
        quantised = np.clip(np.rint(scaled), 0.0, _QUANT_MAX).astype(np.uint8)
    return base64.b64encode(quantised.tobytes()).decode("ascii")


def _stage_names(plants: pd.DataFrame) -> list[str]:
    """Order the stages present in the log along the canonical phenology."""
    present = set(plants["phenological_stage"].dropna().unique())
    ordered = [name for name in STAGE_ORDER if name in present]
    ordered.extend(sorted(name for name in present if name not in STAGE_ORDER))
    return ordered or list(STAGE_ORDER[:1])


def _stage_indices(plants: pd.DataFrame, stages: list[str]) -> pd.Series:
    lookup = {name: index for index, name in enumerate(stages)}
    return plants["phenological_stage"].map(lambda name: lookup.get(str(name), len(stages) - 1))


def _irrigation_series(env: pd.DataFrame, days: int) -> np.ndarray:
    """Recover the daily irrigation depth stamped by the Rancho irrigation step.

    The agronomy model records the dose applied to served cells and the served
    area fraction, so the series reports the field-average depth — the number a
    water budget actually cares about.
    """
    applied = np.zeros(days, dtype=np.float64)
    if "custom_json" not in env.columns:
        return applied
    import json

    for day, payload in zip(env["day"].to_numpy(dtype=np.int64), env["custom_json"], strict=False):
        if not isinstance(payload, str) or not payload:
            continue
        try:
            custom = json.loads(payload)
        except json.JSONDecodeError:
            continue
        dose = custom.get("irrigation_mm")
        share = custom.get("irrigation_frac", 1.0)
        if isinstance(dose, int | float) and isinstance(share, int | float) and 0 < day <= days:
            applied[day - 1] = float(dose) * float(share)
    return applied


def _crop_yields(
    cfg: ScenarioConfig,
    final_biomass_g: np.ndarray,
) -> tuple[list[dict[str, Any]], float, float]:
    """Marketable yield of every planted crop, and the field totals.

    A mixed field has no single harvest index or dry-matter fraction, so the two
    conversions are applied per crop over *its own* cells and only then added:
    summing raw numbers would price a lettuce bed as if it were a tomato.  Each
    row reports its own per-hectare figure; the field total is the area-weighted
    mean, which is the number that matches "what did this hectare produce".
    """
    cells = cfg.rows * cfg.cols
    planted = crop_grid(cfg)
    keys = crop_keys(cfg)

    rows: list[dict[str, Any]] = []
    total_kg = 0.0
    weighted = 0.0
    for key in keys:
        mask = np.asarray([crop == key for crop in planted], dtype=bool)
        count = int(np.count_nonzero(mask))
        if count == 0:
            continue
        profile = CROP_PROFILES[key]
        index = cfg.harvest_index if cfg.harvest_index is not None else profile.harvest_index
        mean_biomass_g = float(np.mean(final_biomass_g[mask]))
        biomass_t_ha = mean_biomass_g * cfg.plants_per_m2 * 10.0 / 1000.0
        harvested_dry_t_ha = biomass_t_ha * index
        fresh_t_ha = harvested_dry_t_ha / profile.dry_matter_fraction * profile.marketable_fraction
        crop_kg = fresh_t_ha * count / 10_000.0 * 1000.0
        total_kg += crop_kg
        weighted += fresh_t_ha * count / max(1, cells)
        rows.append(
            {
                "key": key,
                "label": profile.label,
                "cells": count,
                "share": round(count / max(1, cells), 4),
                "harvest_index": round(index, 3),
                "biomass_t_per_ha": round(biomass_t_ha, 3),
                "t_per_ha": round(fresh_t_ha, 3),
                "total_kg": round(crop_kg, 1),
            }
        )
    return rows, round(weighted, 3), round(total_kg, 1)


def build_bundle(
    *,
    cfg: ScenarioConfig,
    log_dir: Path,
    elevation: np.ndarray,
    yield_summary: dict[str, Any] | None,
    elapsed_s: float,
    run_id: str,
    created_at: str,
    weather: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the payload consumed by the WebGL client."""
    days, rows, cols = cfg.days, cfg.rows, cfg.cols

    plants = _read_table(log_dir, "plants")
    soil = _read_table(log_dir, "soil")
    environment = _read_table(log_dir, "environment")

    top_layer = soil[soil["layer"] == soil["layer"].min()]
    stages = _stage_names(plants)

    stack: dict[str, np.ndarray] = {
        "lai": _fill_missing(_pivot(plants, "lai", days=days, rows=rows, cols=cols)),
        "biomass": _fill_missing(_pivot(plants, "biomass_g", days=days, rows=rows, cols=cols)),
        "height": _fill_missing(_pivot(plants, "height_cm", days=days, rows=rows, cols=cols)),
        "stress": _fill_missing(_pivot(plants, "stress_index", days=days, rows=rows, cols=cols)),
        "moisture": _fill_missing(
            _pivot(top_layer, "moisture_pct", days=days, rows=rows, cols=cols)
        ),
        "nitrogen": _fill_missing(
            _pivot(top_layer, "nitrogen_kg_ha", days=days, rows=rows, cols=cols)
        ),
        "disease": _fill_missing(
            _pivot(plants, "disease_severity", days=days, rows=rows, cols=cols)
        ),
    }

    stage_values = np.full((days, rows * cols), 0.0, dtype=np.float64)
    stage_index = _stage_indices(plants, stages).to_numpy(dtype=np.float64)
    cell_index = plants["row"].to_numpy(dtype=np.int64) * cols + plants["col"].to_numpy(
        dtype=np.int64
    )
    day_index = plants["day"].to_numpy(dtype=np.int64) - 1
    stage_values[day_index, cell_index] = stage_index
    stacks: dict[str, np.ndarray] = {**stack, "stage": stage_values}

    domains: dict[str, list[float]] = {}
    for spec in CHANNELS:
        values = stacks[spec.name]
        if spec.kind == "stage":
            domains[spec.name] = [0.0, max(1.0, float(len(stages) - 1))]
        else:
            domains[spec.name] = [float(np.nanmin(values)), float(np.nanmax(values))]

    encoded: dict[str, list[str]] = {
        spec.name: [_encode(stacks[spec.name][day], *domains[spec.name]) for day in range(days)]
        for spec in CHANNELS
    }

    rain = environment["rainfall_mm"].to_numpy(dtype=np.float64)
    et0 = environment["et0_mm"].to_numpy(dtype=np.float64)
    tmean = environment["temp_mean_c"].to_numpy(dtype=np.float64)
    doy = environment["doy"].to_numpy(dtype=np.int64)
    irrigation = _irrigation_series(environment, days)

    alive = plants["alive"].to_numpy(dtype=bool)
    alive_frac = np.ones(days, dtype=np.float64)
    if alive.size:
        alive_days = plants.loc[alive, "day"].value_counts()
        for day, count in alive_days.items():
            if 1 <= day <= days:
                alive_frac[day - 1] = float(count) / (rows * cols)

    series: list[dict[str, float | int]] = []
    for day in range(days):
        series.append(
            {
                "day": day + 1,
                "doy": int(doy[day]) if day < doy.size else 0,
                "tmean": round(float(tmean[day]), 2) if day < tmean.size else 0.0,
                "rain": round(float(rain[day]), 2) if day < rain.size else 0.0,
                "irrigation": round(float(irrigation[day]), 2),
                "et0": round(float(et0[day]), 3) if day < et0.size else 0.0,
                "lai": round(float(np.mean(stacks["lai"][day])), 3),
                "biomass": round(float(np.mean(stacks["biomass"][day])), 3),
                "stress": round(float(np.mean(stacks["stress"][day])), 3),
                "moisture": round(float(np.mean(stacks["moisture"][day])), 2),
                "nitrogen": round(float(np.mean(stacks["nitrogen"][day])), 2),
                "alive": round(float(alive_frac[day]), 4),
            }
        )

    frames: list[dict[str, Any]] = []
    for day in range(days):
        frames.append(
            {
                "day": day + 1,
                "doy": series[day]["doy"],
                "rain": series[day]["rain"],
                "irrigation": series[day]["irrigation"],
                "ch": {spec.name: encoded[spec.name][day] for spec in CHANNELS},
            }
        )

    elevation_values = np.asarray(elevation, dtype=np.float64).reshape(-1)

    # Harvest estimate.  The bundled plugin only partitions *current-season*
    # increment during grain fill and ignores stem-reserve translocation, which
    # under-reports grain by roughly a factor of four.  Rancho therefore reports
    # a harvest-index estimate from final above-ground dry matter, and keeps the
    # engine's own figure alongside it for transparency.
    #
    # For greenhouse vegetables the index gives fruit *dry matter*; marketable
    # yield is fresh weight, so the crop's dry-matter fraction and the share
    # that actually grades out are applied on top.  With several crops in the
    # field both factors belong to the crop, so they are applied per crop and
    # only then averaged over planted area.
    final_biomass_g = stacks["biomass"][-1] if days else np.zeros(cfg.rows * cfg.cols)
    per_crop, estimated_t_ha, total_kg = _crop_yields(cfg, final_biomass_g)
    mean_biomass_g = float(np.mean(final_biomass_g)) if days else 0.0
    biomass_t_ha = mean_biomass_g * cfg.plants_per_m2 * 10.0 / 1000.0
    profile = CROP_PROFILES[cfg.crop]
    engine_t_ha = round(float((yield_summary or {}).get("yield_t_per_ha", 0.0)), 3)
    harvest_index = cfg.harvest_index if cfg.harvest_index is not None else profile.harvest_index

    return {
        "id": run_id,
        "created_at": created_at,
        "meta": {
            "name": cfg.name,
            "rows": rows,
            "cols": cols,
            "days": days,
            "crop": cfg.crop,
            "crop_label": CROP_PROFILES[cfg.crop].label,
            "variety": cfg.variety,
            "sowing_doy": cfg.sowing_doy,
            "soil": cfg.soil,
            "climate": cfg.climate,
            "terrain": cfg.terrain,
            "slope_pct": cfg.slope_pct,
            "protected": cfg.protected,
            "cover_transmission": round(cfg.cover_transmission, 2),
            "irrigation": cfg.irrigation,
            "irrigation_mm": cfg.irrigation_mm,
            "irrigation_coverage": round(cfg.irrigation_coverage, 2),
            "fertiliser_kg_ha": cfg.fertiliser_kg_ha,
            "plants_per_m2": round(cfg.plants_per_m2, 2),
            "area_ha": round(rows * cols / 10_000.0, 4),
            "seed": cfg.seed,
            "elapsed_s": round(elapsed_s, 2),
            "physics": cfg.physics.model_dump(),
            "weather": weather or {},
            "mosaic": len(crop_keys(cfg)) > 1,
            # Every crop planted in this run, in first-appearance order — the
            # client indexes `grid.crop_index` into this list.
            "crops": [{"key": key, "label": CROP_PROFILES[key].label} for key in crop_keys(cfg)],
        },
        "grid": {
            "rows": rows,
            "cols": cols,
            "cell_size_m": 1.0,
            "area_ha": round(rows * cols / 10_000.0, 4),
            "plant_spacing_m": cfg.plant_spacing_m,
            "row_spacing_m": cfg.row_spacing_m,
            "elevation": [round(float(value), 3) for value in elevation_values],
            "crop_index": crop_indices(cfg),
        },
        "channels": {
            spec.name: {
                "label": spec.label,
                "unit": spec.unit,
                "kind": spec.kind,
                "domain": domains[spec.name],
            }
            for spec in CHANNELS
        },
        "stages": [{"key": name, "label": STAGE_LABELS.get(name, name)} for name in stages],
        "series": series,
        "frames": frames,
        "yield": {
            "t_per_ha": round(estimated_t_ha, 3),
            "total_kg": total_kg,
            "biomass_t_per_ha": round(biomass_t_ha, 3),
            "harvest_index": round(harvest_index, 3),
            "engine_t_per_ha": engine_t_ha,
            "crops": per_crop,
        },
    }
