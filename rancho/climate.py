"""Synthetic weather and soil inputs for CropForge.

CropForge consumes daily weather and soil profiles as CSV files.  Rancho keeps
runs reproducible by *generating* those inputs from a seeded model instead of
shipping opaque data files: same scenario + same seed ⇒ bit-identical weather
and identical soil columns.  The generated soil is also handed back as a
:class:`SoilField` so the agronomy model and the engine work from one truth.

Weather generation returns the shared :class:`rancho.weather.WeatherSeries`;
serialisation lives in :mod:`rancho.weather` so the synthetic path and the
real-archive path cannot drift apart.
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
from rancho.weather import WeatherDay, WeatherSeries

#: Synthetic weather has no calendar, so it is pinned to one epoch.  The
#: seasonal sine only reads a day-of-year, which keeps every preset's CSV
#: reproducible byte for byte no matter when the run happens.
SYNTHETIC_EPOCH: Final = date(2026, 1, 1)

#: One layer, not three.  CropForge logs one row per (cell, layer) *per day*,
#: so a three-layer profile triples the daily soil rows — 75 % of every day's
#: log at any grid size — while nothing downstream reads past the surface:
#: :mod:`rancho.agronomy` keeps its own single-layer root-zone bucket and
#: :mod:`rancho.frames` pivots the top layer only.  Dropping the two buried
#: layers is worth roughly 40 % of a run's wall time and changes no number
#: (checked preset by preset).  Revisit only if CropForge's own
#: ``water_balance`` engine is ever switched on — that one does walk the column.
LAYERS: Final[tuple[tuple[float, float], ...]] = ((0.0, 20.0),)

#: Display names for the climate profiles (Chinese UI labels).
CLIMATE_LABELS: Final[dict[ClimateKind, str]] = {
    "temperate": "温带",
    "continental": "大陆性",
    "semi-arid": "半干旱",
    "tropical": "热带",
}


@dataclass(frozen=True, slots=True)
class ClimateProfile:
    """Parameters of one synthetic climate.

    The seasonal cycle is a single sine for temperature and radiation, while
    rainfall is a Bernoulli draw whose probability itself follows a cosine —
    that second harmonic is what turns a Mediterranean-style winter-rain climate
    into a tropical one with a sharp wet season and a genuinely dry half-year.
    """

    base_temp_c: float
    temp_amplitude_k: float
    temp_phase_doy: float
    rain_probability: float
    rain_seasonality: float
    rain_peak_doy: float
    rain_mean_mm: float
    humidity_base_pct: float
    humidity_seasonal_pct: float
    humidity_wet_bonus_pct: float
    radiation_base_mj: float
    radiation_seasonal_mj: float
    wind_base_ms: float
    wind_scale: float


#: One entry per climate.  The first three keep the numbers the presets were
#: calibrated on; ``tropical`` follows El Salvador: 23–27 °C all year, a dry
#: season from November to April and a wet season that delivers ~80 % of the
#: annual 1700 mm between May and October.
CLIMATE_PROFILES: Final[dict[ClimateKind, ClimateProfile]] = {
    "temperate": ClimateProfile(
        18.0, 8.0, 110.0, 0.30, 0.0, 240.0, 5.0, 58.0, 12.0, 18.0, 20.0, 6.0, 1.4, 0.7
    ),
    "continental": ClimateProfile(
        15.0, 11.0, 110.0, 0.17, 0.0, 240.0, 4.5, 58.0, 12.0, 18.0, 20.0, 6.0, 1.4, 0.7
    ),
    "semi-arid": ClimateProfile(
        25.0, 9.0, 110.0, 0.07, 0.0, 240.0, 3.2, 58.0, 12.0, 18.0, 20.0, 6.0, 1.4, 0.7
    ),
    "tropical": ClimateProfile(
        25.0, 2.2, 14.0, 0.18, 0.44, 240.0, 12.0, 72.0, 6.0, 8.0, 18.0, 3.0, 0.6, 0.35
    ),
}

#: texture → (field capacity %, wilting point %, bulk density, topsoil N kg/ha)
#: ``andisol`` is the volcanic ash soil that covers most of the Salvadoran
#: highlands: low bulk density, high water holding, and a large organic N pool.
TEXTURE: Final[dict[SoilKind, tuple[float, float, float, float]]] = {
    "loam": (30.0, 13.0, 1.32, 90.0),
    "clay": (38.0, 20.0, 1.26, 105.0),
    "sandy": (18.0, 7.0, 1.55, 52.0),
    "andisol": (34.0, 16.0, 1.05, 115.0),
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


def generate_weather(
    *,
    days: int,
    climate: ClimateKind,
    seed: int,
    start: date,
) -> WeatherSeries:
    """Generate a deterministic daily weather series starting at *start*.

    Temperature and radiation follow a sine peaking at the profile's phase;
    rain is a Bernoulli draw whose wet-day probability is itself seasonal;
    radiation is damped on wet days.

    Values are kept unrounded: rounding belongs to the CSV writer, which is
    shared with the measured-weather path.
    """
    profile = CLIMATE_PROFILES[climate]
    rng = random.Random(seed)

    series: list[WeatherDay] = []
    wet = False
    for offset in range(days):
        current = start + timedelta(days=offset)
        doy = current.timetuple().tm_yday
        seasonal = math.sin(2.0 * math.pi * (doy - profile.temp_phase_doy) / 365.0)
        rain_phase = math.cos(2.0 * math.pi * (doy - profile.rain_peak_doy) / 365.0)
        rain_chance = min(
            0.95, max(0.02, profile.rain_probability + profile.rain_seasonality * rain_phase)
        )

        wet = rng.random() < rain_chance or (wet and rng.random() < rain_chance * 0.55)
        rainfall = rng.expovariate(1.0 / profile.rain_mean_mm) if wet else 0.0
        rainfall = round(min(rainfall, 45.0), 1)

        tmean = profile.base_temp_c + profile.temp_amplitude_k * seasonal + rng.gauss(0.0, 1.6)
        spread = 8.0 + 2.5 * (1.0 - seasonal) + rng.uniform(0.0, 2.5)
        tmax = tmean + spread / 2.0
        tmin = tmean - spread / 2.0

        radiation = max(
            2.0,
            profile.radiation_base_mj
            + profile.radiation_seasonal_mj * seasonal
            - 1.4 * rainfall
            + rng.gauss(0.0, 1.8),
        )
        humidity = min(
            96.0,
            max(
                22.0,
                profile.humidity_base_pct
                + profile.humidity_seasonal_pct * (1.0 - seasonal)
                + (profile.humidity_wet_bonus_pct if wet else 0.0),
            ),
        )
        wind = max(0.3, profile.wind_base_ms + rng.gammavariate(1.6, profile.wind_scale))

        series.append(
            WeatherDay(
                day=current,
                tmax_c=tmax,
                tmin_c=tmin,
                radiation_mj=radiation,
                rainfall_mm=rainfall,
                humidity_pct=humidity,
                wind_ms=wind,
            )
        )

    return WeatherSeries(
        days=tuple(series),
        source="synthetic",
        label=f"合成气候 · {CLIMATE_LABELS[climate]}",
        provider="Rancho 合成气候",
    )


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
