"""Synthetic weather and soil inputs for CropForge.

CropForge consumes daily weather and soil profiles as CSV files.  Rancho keeps
runs reproducible by *generating* those inputs from a seeded model instead of
shipping opaque data files: same scenario + same seed ⇒ bit-identical weather
and identical soil columns.  The generated soil is also handed back as a
:class:`SoilField` so the agronomy model and the engine work from one truth.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Final

import numpy as np

from rancho.scenario import ClimateKind, SoilKind, SoilPattern

WEATHER_HEADER: Final = "date,tmax_c,tmin_c,radiation_mj,rainfall_mm,humidity_pct,wind_ms"
LAYERS: Final[tuple[tuple[float, float], ...]] = ((0.0, 20.0), (20.0, 50.0), (50.0, 90.0))

#: (mean temperature °C, seasonal amplitude K, rain probability, mean rain mm)
CLIMATE_PROFILES: Final[dict[ClimateKind, tuple[float, float, float, float]]] = {
    "temperate": (18.0, 8.0, 0.30, 5.0),
    "continental": (15.0, 11.0, 0.17, 4.5),
    "semi-arid": (25.0, 9.0, 0.07, 3.2),
}

#: texture → (field capacity %, wilting point %, bulk density, topsoil N kg/ha)
TEXTURE: Final[dict[SoilKind, tuple[float, float, float, float]]] = {
    "loam": (30.0, 13.0, 1.32, 90.0),
    "clay": (38.0, 20.0, 1.26, 105.0),
    "sandy": (18.0, 7.0, 1.55, 52.0),
}


@dataclass(frozen=True, slots=True)
class SoilField:
    """Per-cell soil state shared by the CSV writer and the agronomy model."""

    rows: int
    cols: int
    moisture: np.ndarray
    nitrogen: np.ndarray
    field_capacity: np.ndarray
    wilting_point: np.ndarray
    bulk_density: np.ndarray
    elevation: np.ndarray


def write_weather_csv(
    path: Path,
    *,
    days: int,
    climate: ClimateKind,
    seed: int,
    start_doy: int,
) -> Path:
    """Generate a deterministic daily weather series starting at *start_doy*.

    The seasonal cycle follows a northern-hemisphere shape peaking around
    day 200; rain is a Bernoulli draw whose wet days add exponentially
    distributed depth.  Radiation is damped on wet days.
    """
    base, amplitude, rain_prob, rain_mean = CLIMATE_PROFILES[climate]
    rng = random.Random(seed)
    origin = date(2026, 1, 1) + timedelta(days=start_doy - 1)

    lines: list[str] = [WEATHER_HEADER]
    wet = False
    for offset in range(days):
        current = origin + timedelta(days=offset)
        doy = current.timetuple().tm_yday
        seasonal = math.sin(2.0 * math.pi * (doy - 110) / 365.0)

        wet = rng.random() < rain_prob or (wet and rng.random() < rain_prob * 0.55)
        rainfall = rng.expovariate(1.0 / rain_mean) if wet else 0.0
        rainfall = round(min(rainfall, 45.0), 1)

        tmean = base + amplitude * seasonal + rng.gauss(0.0, 1.6)
        spread = 8.0 + 2.5 * (1.0 - seasonal) + rng.uniform(0.0, 2.5)
        tmax = tmean + spread / 2.0
        tmin = tmean - spread / 2.0

        radiation = max(2.0, 20.0 + 6.0 * seasonal - 1.4 * rainfall + rng.gauss(0.0, 1.8))
        humidity = min(96.0, max(22.0, 58.0 + 12.0 * (1.0 - seasonal) + (18.0 if wet else 0.0)))
        wind = max(0.3, 1.4 + rng.gammavariate(1.6, 0.7))

        lines.append(
            f"{current.isoformat()},{tmax:.1f},{tmin:.1f},{radiation:.1f},"
            f"{rainfall:.1f},{humidity:.0f},{wind:.1f}"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _management_zones(
    rows: int,
    cols: int,
    *,
    pattern: SoilPattern,
    seed: int,
) -> np.ndarray:
    """Smooth, seeded fertility field in ``[-0.5, 0.5]`` — one value per cell.

    Real fields are never uniform: traffic lanes compact, headlands erode,
    manure lands in patches.  Modelling that explicitly is what makes the grid
    worth rendering, so the pattern is a first-class scenario input.
    """
    if pattern == "uniform":
        return np.zeros((rows, cols), dtype=np.float64)

    row_axis = np.linspace(0.0, 1.0, rows, dtype=np.float64)[:, None]
    col_axis = np.linspace(0.0, 1.0, cols, dtype=np.float64)[None, :]

    if pattern == "gradient":
        # Fertility decays downhill — topsoil creep plus harvest traffic.
        field = 0.62 * (0.5 - row_axis) + 0.38 * (0.5 - col_axis)
        return np.asarray(field, dtype=np.float64)

    rng = np.random.default_rng(seed)
    phases = rng.uniform(0.0, 2.0 * np.pi, size=3)
    field = (
        0.55
        * np.sin(2.0 * np.pi * (1.15 * row_axis) + phases[0])
        * np.cos(2.0 * np.pi * (0.95 * col_axis) + phases[1])
        + 0.30 * np.sin(2.0 * np.pi * (2.40 * row_axis + 1.70 * col_axis) + phases[2])
        + 0.15 * np.cos(2.0 * np.pi * (3.60 * col_axis) + phases[0] * 1.7)
    )
    peak = float(np.max(np.abs(field))) or 1.0
    return np.asarray(field / (2.0 * peak), dtype=np.float64)


def build_soil_field(
    soil: SoilKind,
    *,
    rows: int,
    cols: int,
    pattern: SoilPattern,
    variation: float,
    seed: int,
    elevation: np.ndarray,
) -> SoilField:
    """Build the per-cell soil columns (moisture, N, FC, WP, density)."""
    field_capacity, wilting_point, bulk, nitrogen = TEXTURE[soil]
    zones = _management_zones(rows, cols, pattern=pattern, seed=seed)
    jitter = np.random.default_rng(seed + 977).normal(0.0, 0.02, size=(rows, cols))
    factor = zones * variation
    noise = factor + jitter

    return SoilField(
        rows=rows,
        cols=cols,
        moisture=np.asarray(field_capacity * 0.62 + 4.2 * noise, dtype=np.float64),
        nitrogen=np.asarray(nitrogen * (1.0 + 0.55 * noise), dtype=np.float64),
        field_capacity=np.asarray(field_capacity * (1.0 + 0.19 * factor), dtype=np.float64),
        wilting_point=np.asarray(wilting_point * (1.0 + 0.24 * factor), dtype=np.float64),
        bulk_density=np.asarray(bulk * (1.0 + 0.08 * factor), dtype=np.float64),
        elevation=np.asarray(elevation, dtype=np.float64),
    )


def write_soil_csv(path: Path, field: SoilField, *, pattern: SoilPattern) -> Path:
    """Serialise a :class:`SoilField` as a CropForge soil CSV.

    ``pattern="uniform"`` writes one layered profile; anything else writes one
    row per (cell, layer) so CropForge builds a spatial soil grid that matches
    what the agronomy model is actually simulating.
    """
    lines = [
        "layer,depth_top_cm,depth_bottom_cm,moisture_pct,n_kg_ha,bulk_density,pen_resistance_mpa"
    ]

    if pattern == "uniform":
        surface = float(field.moisture[0, 0])
        nitrogen = float(field.nitrogen[0, 0])
        bulk = float(field.bulk_density[0, 0])
        for index, (top, bottom) in enumerate(LAYERS):
            lines.append(
                f"{index + 1},{top:.0f},{bottom:.0f},{surface + 1.4 * index:.2f},"
                f"{nitrogen:.1f},{bulk:.2f},{(1.1 + 0.5 * index):.1f}"
            )
    else:
        lines[0] = (
            "row,col,layer,depth_top_cm,depth_bottom_cm,moisture_pct,"
            "n_kg_ha,bulk_density,pen_resistance_mpa"
        )
        for row in range(field.rows):
            for col in range(field.cols):
                surface = float(field.moisture[row, col])
                nitrogen = float(field.nitrogen[row, col])
                bulk = float(field.bulk_density[row, col])
                for index, (top, bottom) in enumerate(LAYERS):
                    lines.append(
                        f"{row},{col},{index + 1},{top:.0f},{bottom:.0f},"
                        f"{surface + 1.4 * index:.2f},{nitrogen:.1f},{bulk:.2f},"
                        f"{(1.1 + 0.5 * index):.1f}"
                    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
