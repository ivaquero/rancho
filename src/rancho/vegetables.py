"""Greenhouse vegetable crop plugins.

CropForge only ships wheat and maize, both built around a cereal ideotype: a
tillering phase, a grain-fill phase and a harvest index applied to grain.  A
protected vegetable crop behaves differently enough that re-parameterising a
cereal would be dishonest, so Rancho supplies its own plugins:

* the phenology is transplant-to-harvest rather than sowing-to-maturity, with
  flowering, fruit set and a long picking period;
* the canopy is trained, so height is capped by the trellis instead of growing
  without bound;
* the harvested organ is a fresh fruit or leaf, which is why yield is converted
  from dry matter in :mod:`rancho.frames` instead of being read off the model.

All three crops share one thermal-time model; only the profile differs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from cropforge._plugins_base import CropPlugin, register_crop

#: How fast the canopy still expands in each stage (1.0 = full rate).  A
#: staked tomato keeps laying down leaves while it is being picked, just slower
#: than during vegetative growth; a lettuce head never stops.
_GROWTH_RATE: dict[str, float] = {
    "germination": 0.6,
    "seedling": 0.6,
    "vegetative": 1.0,
    "flowering": 1.0,
    "fruit_set": 0.8,
    "fruiting": 0.25,
    "maturity": 0.40,
}


@dataclass(frozen=True, slots=True)
class VegetableProfile:
    """Everything that differs between one greenhouse vegetable and another."""

    species: str
    #: Base temperature for thermal time (°C).
    base_temp_c: float
    #: ``(stage, cumulative degree-days)`` — must start at 0.0 and stay ordered.
    stages: tuple[tuple[str, float], ...]
    #: Radiation use efficiency, g dry matter per MJ intercepted.
    rue: float
    #: LAI gained per °C·day while the canopy is still expanding.
    lai_slope: float
    lai_max: float
    #: Daily LAI loss fraction once the crop reaches maturity.
    senescence_rate: float
    #: Stages during which the canopy senesces instead of expanding.  A
    #: greenhouse tomato is pulled at maturity; a lettuce head is cut while
    #: still growing, so its list is empty.
    senescence_stages: tuple[str, ...]
    #: Height allometry: cm per √g of dry matter.
    k_height: float
    #: Trellis / frame height that caps the canopy (cm).
    max_height_cm: float


TOMATO = VegetableProfile(
    species="Solanum lycopersicum",
    base_temp_c=10.0,
    stages=(
        ("germination", 0.0),
        ("emergence", 55.0),
        ("seedling", 110.0),
        ("vegetative", 300.0),
        ("flowering", 500.0),
        ("fruit_set", 600.0),
        ("fruiting", 700.0),
        ("maturity", 1900.0),
    ),
    rue=1.55,
    lai_slope=0.0045,
    lai_max=3.4,
    senescence_rate=0.012,
    senescence_stages=("maturity",),
    k_height=8.0,
    max_height_cm=220.0,
)

CUCUMBER = VegetableProfile(
    species="Cucumis sativus",
    base_temp_c=12.0,
    stages=(
        ("germination", 0.0),
        ("emergence", 50.0),
        ("seedling", 100.0),
        ("vegetative", 220.0),
        ("flowering", 330.0),
        ("fruit_set", 380.0),
        ("fruiting", 430.0),
        ("maturity", 900.0),
    ),
    rue=1.45,
    lai_slope=0.0070,
    lai_max=3.8,
    senescence_rate=0.015,
    senescence_stages=("maturity",),
    k_height=9.0,
    max_height_cm=250.0,
)

LETTUCE = VegetableProfile(
    species="Lactuca sativa",
    base_temp_c=4.0,
    stages=(
        ("germination", 0.0),
        ("seedling", 60.0),
        ("vegetative", 180.0),
        ("maturity", 520.0),
    ),
    rue=0.80,
    lai_slope=0.0035,
    lai_max=4.5,
    senescence_rate=0.020,
    senescence_stages=(),
    k_height=3.2,
    max_height_cm=30.0,
)


class GreenhouseVegetable(CropPlugin):
    """Thermal-time phenology + RUE growth for a protected vegetable crop."""

    profile: VegetableProfile

    def __init__(self, rue: float | None = None) -> None:
        self.rue = self.profile.rue if rue is None else rue

    # ------------------------------------------------------------------
    # CropPlugin interface
    # ------------------------------------------------------------------

    def step(self, state, env):
        """Advance every plant one day: phenology → canopy → biomass → height."""
        for plant in state.plants:
            if not plant.alive:
                continue
            stage = self._update_phenology(plant, env)
            self._update_canopy(plant, env, stage)
            self._accumulate_biomass(plant)
            self._update_height(plant)
        return state

    # ------------------------------------------------------------------
    # Sub-models
    # ------------------------------------------------------------------

    def _update_phenology(self, plant, env) -> str:
        """Accumulate thermal time and resolve the current stage."""
        profile = self.profile
        tt_today = max(0.0, float(env.temp_mean_c) - profile.base_temp_c)
        thermal_time = float(plant.custom.get("thermal_time", 0.0)) + tt_today
        plant.custom["thermal_time"] = thermal_time

        stage = profile.stages[0][0]
        start_tt = profile.stages[0][1]
        end_tt = start_tt + 200.0
        for index, (name, threshold) in enumerate(profile.stages):
            if thermal_time >= threshold:
                stage = name
                start_tt = threshold
                end_tt = (
                    profile.stages[index + 1][1]
                    if index + 1 < len(profile.stages)
                    else threshold + 200.0
                )

        plant.phenological_stage = stage
        plant.custom["phenological_stage"] = stage
        plant.stage_progress = min(
            1.0, max(0.0, (thermal_time - start_tt) / max(1e-6, end_tt - start_tt))
        )
        return stage

    def _update_canopy(self, plant, env, stage: str) -> None:
        """Canopy expansion until the trellis closes, then senescence."""
        profile = self.profile
        tt_today = max(0.0, float(env.temp_mean_c) - profile.base_temp_c)

        if stage in profile.senescence_stages:
            plant.lai = max(0.0, plant.lai * (1.0 - profile.senescence_rate))
            return

        gain = tt_today * profile.lai_slope * _GROWTH_RATE.get(stage, 0.0)
        plant.lai = min(profile.lai_max, plant.lai + gain)

    def _accumulate_biomass(self, plant) -> None:
        """RUE × intercepted radiation × water and nitrogen stress."""
        ks = float(plant.custom.get("water_stress_ks", 1.0))
        kn = float(plant.custom.get("n_stress_kn", 1.0))
        intercepted = float(plant.custom.get("intercepted_par_mj", 0.0))
        plant.biomass_g += max(0.0, self.rue * intercepted * ks * kn)

    def _update_height(self, plant) -> None:
        """Trained plants stop at the wire; young plants follow √biomass."""
        height = self.profile.k_height * math.sqrt(max(0.0, float(plant.biomass_g)))
        plant.height_cm = min(self.profile.max_height_cm, height)


@register_crop("tomato")
class Tomato(GreenhouseVegetable):
    """Protected tomato, indeterminate, staked to 2.2 m."""

    species = TOMATO.species
    profile = TOMATO


@register_crop("cucumber")
class Cucumber(GreenhouseVegetable):
    """Protected cucumber on a vertical trellis."""

    species = CUCUMBER.species
    profile = CUCUMBER


@register_crop("lettuce")
class Lettuce(GreenhouseVegetable):
    """Short-cycle leafy crop; the frame caps it at 30 cm."""

    species = LETTUCE.species
    profile = LETTUCE
