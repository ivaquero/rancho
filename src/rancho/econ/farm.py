"""Driving CropForge's ``Farm`` from inside the market loop.

Phase 2 asked the crop *plugin* for its production function.  That was already
CropForge's code, but it was a plugin stepped by hand: one plant, no field, no
grid, no physics hooks — the numbers came out of the same equations the engine
uses, without the engine.

Phase 3 runs the farm.  For every crop the probe now owns a small
``6 × 6`` field, hands CropForge a weather series built from the *market's*
delivered light, injects the market's own water and nitrogen stress into
``plant.custom``, and reads the daily dry-matter increment back out of the
simulation.  Nothing about growth is re-derived here any more.

That closes a loop the previous two phases could not even express:

    market allocates light ──▶ Farm grows the crop with that light
            ▲                              │
            └──── measured marginal value ─┘

A single pass cannot close it, because the market needs a price *before* the
harvest exists.  So the coupling iterates: the previous season's Farm output
becomes this season's response curve, the market re-clears against it, and the
loop stops when total yield settles.

Three numbers worth remembering when reading the output:

* **A Farm season is cheap**, once its state log is out of the way.  Twenty-eight
  days on a 36-cell field costs about ten milliseconds, so a two-pass probe over
  six arms and two tariffs is under a second.  Over 90 % of a season used to be
  CropForge encoding a Parquet dataset that this module then deleted unread; see
  ``_Unlogged``.  The field is a *bed-scale stand-in*: every quantity is per m²,
  and the grid only exists so the physics hooks have cells to write into.
* **The perturbation is seasonal, not diurnal.**  The marginal value of a photon
  is measured by re-running the whole season at ±10 % radiation and differencing
  each day's increment.  Because the canopy here is driven by thermal time and
  biomass feeds nothing back, that difference *is* the day-local derivative.  A
  plugin with light-dependent canopy growth would make it a total derivative,
  which overstates today's marginal — see ``docs/economics.md``.
* **The Farm measures potential production.**  ``Ks`` and ``Kn`` are left at 1.0
  inside the physics run and applied once, downstream, by the settlement, for
  the same reason the plugin backend pins them: the balance the crops are
  *paying* for must be the only one that binds.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Final

from cropforge import Crop, Farm, Field, Weather
from cropforge.loaders import Soil
from cropforge.logger import StateLogger

from rancho.agronomy import CROP_WATER
from rancho.econ.growth import (
    PLUGINS,
    GrowthFactory,
    LightResponse,
    build_growth,
    transplant_stage,
)
from rancho.econ.spec import MOL_PAR_PER_MJ, CropSpec

#: Side of the square field the physics layer runs on.  One plant per cell, so
#: the grid is also the plant count and every result is already per m².
FARM_GRID = 6
#: Relative light perturbation used to measure the marginal value of a photon.
LIGHT_PROBE = 0.10
#: Coupling passes before the loop is stopped regardless of the residual.
MAX_PASSES = 3
#: Stop early when no arm's total yield moves more than this between passes.
CONVERGENCE = 0.01

_WEATHER_HEADER = "date,tmax_c,tmin_c,radiation_mj,rainfall_mm,humidity_pct,wind_ms"
_SOIL_HEADER = (
    "layer,depth_top_cm,depth_bottom_cm,moisture_pct,n_kg_ha,bulk_density,pen_resistance_mpa"
)
_LAYERS = ((0, 20), (20, 50), (50, 90))
#: Synthetic seasons have no calendar; the plugin only reads the day counter.
_EPOCH = date(2026, 1, 1)

#: CropForge writes its Parquet log relative to the working directory, so runs
#: are serialised — ``chdir`` is process-wide.
_RUN_LOCK = threading.Lock()


class _Unlogged(StateLogger):
    """The session logger, minus the session.

    Every ``Farm.run`` builds a partitioned Parquet dataset of the whole field
    and the probe deletes it unread one call later.  At this field size encoding
    it costs about twelve times what the physics it records costs, plus the same
    again to delete the temporary tree, so the two methods that produce it are
    stubbed out for the run.
    Everything else is inherited: the session directory, ``log_path`` and the
    terrain dump still happen, and any consumer of the log would simply find an
    empty dataset.

    shortcut: CropForge's logging API, not a supported switch — if the probe
    ever needs the log back, delete this class and the guard in ``_simulate``.
    """

    def record(self, *args: Any, **kwargs: Any) -> None:
        return None

    def flush(self) -> None:
        return None


@contextmanager
def _unlogged() -> Generator[None]:
    """Install :class:`_Unlogged` where ``Farm.run`` imports it from."""
    import cropforge.logger as logger_module

    saved = logger_module.StateLogger
    logger_module.StateLogger = _Unlogged  # ty: ignore[invalid-assignment] - the patch
    try:
        yield
    finally:
        logger_module.StateLogger = saved


@dataclass(frozen=True, slots=True)
class FarmSeason:
    """How the physics half of the loop is configured.

    Identical for every arm: only the weather the market delivers differs, which
    is what makes the arms comparable.
    """

    grid: int = FARM_GRID


#: Shared immutable default, so signature defaults do not build an object.
DEFAULT_SEASON: Final = FarmSeason()


@dataclass
class FarmTable:
    """One crop's measured response curve: a piecewise-linear light response.

    Built from three Farm seasons (baseline, +10 %, −10 % radiation).  The
    level comes from the baseline run; the slope from the two perturbations, so
    the bid prices the physics layer's own measurement instead of an analytic
    derivative this module would have to keep in sync by hand.
    """

    spec: CropSpec
    base: tuple[float, ...]
    slope: tuple[float, ...]
    reference: tuple[float, ...]
    #: Leaf area index the baseline season ended on — the canopy the measured
    #: response belongs to, and the only trace of it this backend keeps.
    final_lai: float = 0.0
    day: int = 0
    name: str = "farm"

    def production(self, dli: float) -> float:
        """Marketable fresh mass at *dli*, extrapolated from the anchor."""
        index = self._index()
        value = self.base[index] + self.slope[index] * (dli - self.reference[index])
        return max(0.0, value)

    def marginal(self, dli: float) -> float:
        """g fresh per mol at the current day, as measured by the Farm."""
        return max(0.0, self.slope[self._index()])

    def settle(self, dli: float, temp_mean_c: float) -> None:
        """Advance one day; the anchors themselves are fixed for this pass."""
        self.day += 1

    @property
    def lai(self) -> float:
        """Canopy at the end of the season the table was measured on."""
        return self.final_lai

    def _index(self) -> int:
        return min(self.day, len(self.base) - 1)


@dataclass(frozen=True, slots=True)
class PassReport:
    """What one coupling pass cost and how far it moved."""

    index: int
    yield_kg: dict[str, float]
    residual: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "pass": self.index,
            "yield_kg": {key: round(value, 2) for key, value in self.yield_kg.items()},
            "residual": round(self.residual, 5),
        }


@dataclass(slots=True)
class Season:
    """The per-day weather a Farm run consumes.

    No stress columns: :mod:`rancho.econ.growth` established that the crop
    plugin is stepped *potential* and the economic layer applies ``Ks·Kn`` once,
    so the same rule holds here — otherwise the penalty lands twice, once in
    the physics and once in the settlement.
    """

    radiation_mj: Sequence[float]
    temp_mean: Sequence[float]
    temp_max: Sequence[float]

    def __post_init__(self) -> None:
        days = len(self.radiation_mj)
        for name in ("temp_mean", "temp_max"):
            if len(getattr(self, name)) != days:
                raise ValueError(f"{name} has {len(getattr(self, name))} days, expected {days}")

    @property
    def days(self) -> int:
        return len(self.radiation_mj)


@dataclass(frozen=True, slots=True)
class SeasonRun:
    """One Farm season's output: daily dry matter, and the canopy it ended on."""

    dry_matter: tuple[float, ...]
    final_lai: float


def run_season(
    spec: CropSpec,
    season: Season,
    *,
    light_scale: float = 1.0,
    setup: FarmSeason = DEFAULT_SEASON,
) -> tuple[float, ...]:
    """Run one CropForge season and return daily dry matter, g/m²/day.

    ``light_scale`` multiplies every day's radiation — the perturbation the
    marginal value is read from.  Everything else (temperature, soil, planting)
    is held fixed, so the difference between two runs is attributable to light
    alone.
    """
    return season_run(spec, season, light_scale=light_scale, setup=setup).dry_matter


def season_run(
    spec: CropSpec,
    season: Season,
    *,
    light_scale: float = 1.0,
    setup: FarmSeason = DEFAULT_SEASON,
) -> SeasonRun:
    """Same run as :func:`run_season`, keeping the end-of-season leaf area."""
    cumulative, lai = _simulate(spec, season, light_scale=light_scale, setup=setup)
    increments = [cumulative[0]]
    increments.extend(cumulative[i] - cumulative[i - 1] for i in range(1, len(cumulative)))
    return SeasonRun(
        # Numerical noise around zero is not negative growth.
        dry_matter=tuple(max(0.0, value) for value in increments),
        final_lai=lai,
    )


def measure_table(
    spec: CropSpec,
    season: Season,
    *,
    setup: FarmSeason = DEFAULT_SEASON,
    light_probe: float = LIGHT_PROBE,
) -> FarmTable:
    """Measure one crop's light response by three Farm seasons.

    Returns the baseline level in marketable fresh mass (dry matter scaled by
    the crop's dry-matter fraction) and the central-difference slope.
    """
    baseline = season_run(spec, season, light_scale=1.0, setup=setup)
    base = baseline.dry_matter
    upper = run_season(spec, season, light_scale=1.0 + light_probe, setup=setup)
    lower = run_season(spec, season, light_scale=1.0 - light_probe, setup=setup)

    fresh = _fresh_per_dry(spec)
    reference = tuple(max(0.0, value) * MOL_PAR_PER_MJ for value in season.radiation_mj)
    slopes = []
    for index, ref in enumerate(reference):
        span = 2.0 * light_probe * ref
        slopes.append((upper[index] - lower[index]) * fresh / span if span > 1e-9 else 0.0)

    return FarmTable(
        spec=spec,
        base=tuple(value * fresh for value in base),
        slope=tuple(slopes),
        reference=reference,
        final_lai=baseline.final_lai,
    )


def measure_from_arm(
    result: Any,
    spec: CropSpec,
    *,
    setup: FarmSeason = DEFAULT_SEASON,
    light_probe: float = LIGHT_PROBE,
) -> FarmTable:
    """Measure the response a finished market arm implies.

    The arm's own daily records supply the weather (delivered light, mean and
    maximum temperature) and the stress the crops actually received, so the Farm
    season being measured is the one the market paid for — not a reference
    season that happens to have the same length.
    """
    rows = sorted((row for row in result.records if row.crop == spec.key), key=lambda r: r.day)
    if not rows:
        raise ValueError(f"arm {result.arm!r} has no records for crop {spec.key!r}")
    season = Season(
        radiation_mj=tuple(row.dli_effective / MOL_PAR_PER_MJ for row in rows),
        temp_mean=tuple(row.temp_mean_c for row in rows),
        temp_max=tuple(row.temp_max_c for row in rows),
    )
    return measure_table(spec, season, setup=setup, light_probe=light_probe)


def _simulate(
    spec: CropSpec,
    season: Season,
    *,
    light_scale: float,
    setup: FarmSeason,
) -> tuple[list[float], float]:
    """Run the Farm: cumulative dry matter per m² and the closing leaf area."""
    days = season.days
    area = float(setup.grid * setup.grid)
    cumulative: list[float] = []
    canopy: list[float] = []

    with TemporaryDirectory(prefix="rancho-econ-") as tmp:
        root = Path(tmp)
        weather_path = root / "weather.csv"
        soil_path = root / "soil.csv"
        _write_weather(weather_path, season, light_scale, days)
        _write_soil(soil_path, spec)

        farm = Farm(name=f"econ_{spec.key}", location=(0.0, 0.0))
        field_ = Field(
            name=f"econ_{spec.key}", rows=setup.grid, cols=setup.grid, area_ha=area / 10_000.0
        )
        field_.set_crop(Crop(species=spec.key, variety="generic", sowing_doy=1))
        field_.set_weather(Weather.from_csv(str(weather_path)))
        field_.set_soil(Soil.from_csv(str(soil_path), apply="uniform"))
        field_.set_planting_config(pattern="rows", row_spacing_m=1.0, plant_spacing_m=1.0)
        farm.add_field(field_)
        # Only the radiation engine: ``et0`` and ``water_balance`` would build a
        # second water balance next to the one the crops are bidding for, and
        # ``disease`` currently writes ``plant.custom['disease_state']`` which
        # the vegetable plugin never reads — enabling it would cost time and
        # change nothing.
        farm.use_physics(radiation=True, k_extinction=CROP_WATER[spec.key].k_extinction)
        field_.use_plugin(PLUGINS[spec.key], phase=2)

        def snapshot(state: Any, env: Any) -> Any:
            """Phase 5: sum the standing biomass into a per-m² dry matter figure."""
            alive = [plant for plant in state.plants if plant.alive]
            cumulative.append(sum(plant.biomass_g for plant in alive) / area)
            canopy.append(sum(plant.lai for plant in alive) / max(1, len(alive)))
            return state

        _seed_seedlings(field_, spec)
        farm.step(phase=5)(snapshot)

        previous = Path.cwd()
        with _RUN_LOCK, _unlogged():
            os.chdir(tmp)
            try:
                farm.run(days=days)
            finally:
                os.chdir(previous)

    if len(cumulative) != days:
        raise RuntimeError(f"Farm logged {len(cumulative)} days, expected {days}")
    return cumulative, canopy[-1] if canopy else 0.0


def _seed_seedlings(field_: Field, spec: CropSpec) -> None:
    """Start the season from transplanted seedlings, not from seed.

    ``Farm`` builds its plants at germination (leaf area ≈ 0.03), and negative
    step phases belong to the physics engines, so there is no public hook that
    runs before the first day's radiation.  Initialising the field state here
    and seeding the canopy is the seam CropForge itself uses in
    ``prepare_next_season``; if that private name ever moves, this fails loudly
    instead of silently growing a season from a seed.
    """
    initialise = getattr(field_, "_init_field_state", None)
    if initialise is None:
        raise RuntimeError(
            "CropForge no longer exposes Field._init_field_state; "
            "the transplanted-seedling start cannot be applied."
        )
    state = initialise(day=1)
    stage, thermal_time = transplant_stage(PLUGINS[spec.key]())
    for plant in state.plants:
        plant.lai = spec.transplant_lai
        plant.phenological_stage = stage
        plant.custom["thermal_time"] = thermal_time
        plant.custom["phenological_stage"] = stage


def _write_weather(path: Path, season: Season, light_scale: float, days: int) -> None:
    lines = [_WEATHER_HEADER]
    for index in range(days):
        mean = float(season.temp_mean[index])
        high = max(float(season.temp_max[index]), mean + 0.5)
        low = 2.0 * mean - high
        radiation = max(0.0, float(season.radiation_mj[index]) * light_scale)
        lines.append(
            f"{(_EPOCH + timedelta(days=index)).isoformat()},{high:.1f},{low:.1f},"
            f"{radiation:.2f},0.0,70,1.4"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_soil(path: Path, spec: CropSpec) -> None:
    lines = [_SOIL_HEADER]
    for index, (top, bottom) in enumerate(_LAYERS):
        lines.append(
            f"{index + 1},{top},{bottom},{spec.initial_moisture_pct + 1.4 * index:.2f},"
            f"{spec.initial_n_kg_ha:.1f},1.32,{1.1 + 0.5 * index:.1f}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fresh_per_dry(spec: CropSpec) -> float:
    """Marketable fresh mass per gram of dry matter."""
    return 1.0 / spec.dry_matter_fraction


@dataclass(slots=True)
class Coupling:
    """One regime's market ↔ Farm iteration.

    ``tables[arm][crop]`` is the response curve measured on the *previous*
    pass; pass 0 has none, so it starts from the plugin backend — the phase-2
    answer, which is also the initial guess a real grower would use.
    """

    setup: FarmSeason
    passes: int = MAX_PASSES
    light_probe: float = LIGHT_PROBE
    tables: dict[str, dict[str, FarmTable]] = field(default_factory=dict)
    history: list[PassReport] = field(default_factory=list)

    def factory(self, arm: str) -> GrowthFactory:
        resolved = self.tables.get(arm, {})

        def build(spec: CropSpec) -> LightResponse:
            table = resolved.get(spec.key)
            if table is None:
                return build_growth("cropforge", spec)
            # A copy per agent: the table carries a day cursor, and handing the
            # same object to two arms would start the second one mid-season.
            return replace(table, day=0)

        return build

    def record(self, index: int, yields: dict[str, float]) -> bool:
        """Append a pass and report whether the loop has converged."""
        previous = self.history[-1].yield_kg if self.history else None
        residual = 0.0
        if previous is not None:
            residual = max(
                abs(value - previous.get(key, value)) / max(1e-9, previous.get(key, value))
                for key, value in yields.items()
            )
        self.history.append(PassReport(index=index, yield_kg=yields, residual=residual))
        return previous is not None and residual < CONVERGENCE

    def refit(self, arm: str, crops: Sequence[CropSpec], result: Any) -> None:
        """Measure the response curves from the arm that just finished."""
        self.tables[arm] = {
            spec.key: measure_from_arm(result, spec, setup=self.setup, light_probe=self.light_probe)
            for spec in crops
        }

    def summary(self) -> dict[str, Any]:
        """Serialisable record of the loop, for ``probe.json`` and the report."""
        last = self.history[-1] if self.history else None
        return {
            "passes": len(self.history),
            "residual": round(last.residual, 5) if last else None,
            "converged": bool(last and last.residual < CONVERGENCE),
            "grid": self.setup.grid,
            "light_probe": self.light_probe,
            "history": [report.as_dict() for report in self.history],
        }
