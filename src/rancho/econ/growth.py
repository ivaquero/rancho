"""Where the daily dry-matter increment comes from.

Phase 1 of the probe answered this with a hand-tuned saturating response,
``g(DLI) = g_max · DLI / (DLI + K)``.  It behaves like a mature canopy from
day one, which made the marginal value of a photon fall smoothly as a crop's
light deficit closed — convenient for the auction, but an assumption rather
than a measurement.

Phase 2 asks CropForge.  Interception is Beer-Lambert on the leaf area index
that Rancho's own crop plugin grows from thermal time, and biomass is
``RUE × intercepted_PAR``.  Two consequences matter for the market:

1. **Production is linear in the light integral, not saturating.**  At a fixed
   canopy, one more kWh is worth a constant number of grams.  The bid curve
   therefore stops being concave, and a crop with a closed canopy will outbid a
   seedling for the same photon.
2. **The canopy is part of the state.**  Early in the season the same photon is
   worth less because there is less leaf to catch it, so light bids rise as the
   crop develops — a dynamic the analytic proxy could not express.

Both backends expose the same three calls — today's light-limited production,
its derivative with respect to the light integral, and an end-of-day update —
so nothing in :mod:`rancho.econ.agents` knows which one it is talking to.

What is deliberately *not* taken from CropForge: the cardinal-temperature
response of assimilation.  Rancho's vegetable plugin models development
(thermal time) but not the temperature dependence of photosynthesis, so the
economic layer keeps its own triangular ``temp_factor`` for that.  Stress
(``Ks``/``Kn``) and quality likewise stay in the economic layer, which is the
only place that owns the water and nitrogen balances in this probe.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from cropforge.physics.radiation import calculate_intercepted_par
from cropforge.state import EnvironmentState, PlantState

from rancho.econ.spec import MOL_PAR_PER_MJ, CropSpec
from rancho.vegetables import Cucumber, GreenhouseVegetable, Lettuce, Tomato

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Crop key → the plugin class CropForge would run for that species.  Using the
#: plugin itself (not a re-implementation) is the point: phenology and canopy
#: expansion are then the same code the physics layer runs.
PLUGINS: dict[str, type[GreenhouseVegetable]] = {
    "tomato": Tomato,
    "cucumber": Cucumber,
    "lettuce": Lettuce,
}

#: Plants per m² used when handing per-plant quantities to the plugin.  Every
#: number this module returns is per m², and the plugin's per-plant biomass is
#: only used for its height allometry, so the value cancels out — it is named
#: anyway so the division below is not mistaken for a physical constant.
BED_DENSITY_PLANTS_M2 = 3.0


class LightResponse(Protocol):
    """The production function the market prices, in marketable fresh mass."""

    @property
    def name(self) -> str:
        """Backend key, as it appears in the run identifier."""
        ...

    def production(self, dli: float) -> float:
        """Light-limited growth today, g fresh / m² / day, before stress."""
        ...

    def marginal(self, dli: float) -> float:
        """Derivative of :meth:`production` with respect to DLI, g fresh / mol."""
        ...

    def settle(self, dli: float, temp_mean_c: float) -> None:
        """Advance the canopy and phenology by one day."""
        ...


#: Callable form of a backend: the market asks for one instance per crop.  A
#: real alias, not a ``TYPE_CHECKING`` one — :func:`resolve_growth` dispatches on
#: it at runtime.
GrowthFactory = Callable[[CropSpec], LightResponse]


@dataclass(frozen=True, slots=True)
class AnalyticGrowth:
    """Phase-1 response: saturating in the light integral, no canopy state."""

    spec: CropSpec
    name: str = "analytic"

    def production(self, dli: float) -> float:
        return self.spec.growth_max_g_m2_day * self._light_factor(dli)

    def marginal(self, dli: float) -> float:
        half = self.spec.dli_half
        return self.spec.growth_max_g_m2_day * half / (dli + half) ** 2

    def settle(self, dli: float, temp_mean_c: float) -> None:
        """Nothing to carry: the response is a pure function of today's DLI."""

    def _light_factor(self, dli: float) -> float:
        return dli / (dli + self.spec.dli_half) if dli > 0.0 else 0.0


@dataclass(frozen=True, slots=True)
class _Bed:
    """The one-plant stand-in for ``FieldState`` the plugin actually reads."""

    plants: Sequence[PlantState]


@dataclass
class CropForgeGrowth:
    """CropForge's production function for one bed.

    The plugin is stepped once per model day with the day's realised light and
    mean temperature, exactly as ``Farm.step(phase=2)`` would after the
    agronomy pass.  Interception is computed *before* the step, from the leaf
    area the plant carried into the day — the same ordering the engine uses.

    Stress is pinned to 1.0 inside the plugin and applied once by the caller:
    the engine counts water and nitrogen twice (canopy shrinkage in
    ``adjust_canopy`` and the ``Ks·Kn`` factor here), which is defensible for
    rendering but would double the yield penalty in an economics probe.
    """

    spec: CropSpec
    plugin: GreenhouseVegetable
    plant: PlantState
    env: EnvironmentState
    k_extinction: float
    name: str = "cropforge"

    @classmethod
    def build(cls, spec: CropSpec) -> CropForgeGrowth:
        from rancho.agronomy import CROP_WATER

        plugin = PLUGINS[spec.key]()
        stage, thermal_time = transplant_stage(plugin)
        plant = PlantState(plant_id=f"{spec.key}-bed", row=0, col=0)
        plant.lai = spec.transplant_lai
        plant.phenological_stage = stage
        plant.custom = {
            "thermal_time": thermal_time,
            "intercepted_par_mj": 0.0,
            "water_stress_ks": 1.0,
            "n_stress_kn": 1.0,
        }
        env = EnvironmentState(
            day=0,
            doy=1,
            temp_max_c=0.0,
            temp_min_c=0.0,
            temp_mean_c=0.0,
            radiation_mj_m2=0.0,
            rainfall_mm=0.0,
            et0_mm=0.0,
            wind_speed_ms=1.0,
            humidity_pct=70.0,
        )
        return cls(
            spec=spec,
            plugin=plugin,
            plant=plant,
            env=env,
            k_extinction=CROP_WATER[spec.key].k_extinction,
        )

    # ------------------------------------------------------------------
    # Production
    # ------------------------------------------------------------------

    @property
    def lai(self) -> float:
        return max(0.0, float(self.plant.lai))

    def canopy_fraction(self) -> float:
        """Share of incoming PAR the canopy intercepts, from Beer-Lambert."""
        return 1.0 - math.exp(-self.k_extinction * self.lai)

    def production(self, dli: float) -> float:
        return self._intercepted(dli) * self._fresh_per_mj()

    def marginal(self, dli: float) -> float:
        """g fresh per mol, by central difference on :meth:`production`.

        Taken numerically on purpose.  The derivative is mathematically
        constant at a fixed canopy, but writing the constant out by hand means
        copying CropForge's internal PAR fraction and extinction convention
        into this module — and any change there would silently desync the bid
        from the production function it is supposed to price.
        """
        step = max(0.01, dli * 5e-3)
        upper = self.production(dli + step)
        lower = self.production(max(0.0, dli - step))
        width = (dli + step) - max(0.0, dli - step)
        return (upper - lower) / width if width > 0.0 else 0.0

    def settle(self, dli: float, temp_mean_c: float) -> None:
        """Hand today's light to the plugin and let it grow the canopy."""
        # Per-plant share, because the plugin divides the canopy's interception
        # across the plants that share the bed.
        self.plant.custom["intercepted_par_mj"] = self._intercepted(dli) / BED_DENSITY_PLANTS_M2
        self.plant.custom["water_stress_ks"] = 1.0
        self.plant.custom["n_stress_kn"] = 1.0
        self.env.radiation_mj_m2 = self._solar_mj(dli)
        self.env.temp_mean_c = temp_mean_c
        self.plugin.step(_Bed(plants=(self.plant,)), self.env)

    def _solar_mj(self, dli: float) -> float:
        """Total solar radiation implied by a PAR integral, MJ/m²/day.

        The forcing generator turned radiation into moles with
        ``PAR_FRACTION × MOL_PER_MJ_PAR``; this is that conversion inverted.
        CropForge then applies its *own* PAR fraction inside
        :func:`calculate_intercepted_par` (0.5 against our 0.45), which is kept
        rather than overridden: its RUE coefficients are calibrated per MJ of
        *its* intercepted PAR, so rewriting the fraction here would bias the
        production level by about 11 %.
        """
        return max(0.0, dli) / MOL_PAR_PER_MJ

    def _intercepted(self, dli: float) -> float:
        """Intercepted PAR in MJ/m²/day at the current canopy."""
        return calculate_intercepted_par(self._solar_mj(dli), self.lai, self.k_extinction)

    def _fresh_per_mj(self) -> float:
        """Marketable fresh mass produced per MJ of intercepted PAR."""
        return self.plugin.rue / self.spec.dry_matter_fraction


def transplant_stage(plugin: GreenhouseVegetable) -> tuple[str, float]:
    """Start the season where a transplanted seedling would be.

    Greenhouses transplant rather than sow, so the bed begins somewhere inside
    the vegetative stage instead of at germination.  The threshold comes from
    the plugin's own stage table, not from a second copy of it.
    """
    stages = plugin.profile.stages
    for name, threshold in stages:
        if name == "vegetative":
            return name, threshold
    return stages[0][0], stages[0][1]


def build_growth(kind: str, spec: CropSpec) -> LightResponse:
    """Construct the production function named by *kind*."""
    if kind == "analytic":
        return AnalyticGrowth(spec)
    if kind == "cropforge":
        return CropForgeGrowth.build(spec)
    raise ValueError(f"unknown growth backend {kind!r}; expected 'analytic' or 'cropforge'")


def resolve_growth(kind: str | GrowthFactory, spec: CropSpec) -> LightResponse:
    """A backend by name, or a callable that builds one per crop.

    The coupled backend cannot be named by a string: its response curve is
    measured per crop, per arm, and changes when the coupling refits.  Passing
    a factory keeps that out of the model — :class:`CropAgent` only ever sees a
    ``LightResponse``.
    """
    if isinstance(kind, str):
        return build_growth(kind, spec)
    return kind(spec)
