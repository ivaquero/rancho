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

All three crops share one thermal-time model; only the profile differs — and
that profile is no longer written here: it is read from the TOML templates in
:mod:`rancho.crops`, which also feed the water balance and the product
description, so one edit still describes one crop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from cropforge._plugins_base import CropPlugin, register_crop

from rancho.crops import TEMPLATES, CropTemplate, CropTemplateError

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
    """Everything that differs between one greenhouse vegetable and another.

    This is the flat, runtime view of :class:`rancho.crops.CropTemplate`: the
    plugin reads one level deep, the template keeps the same numbers grouped by
    what they describe.  Do not edit the constants below — edit the TOML.
    """

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


def _profile(template: CropTemplate) -> VegetableProfile:
    """Flatten one template into the runtime view the plugin reads."""
    growth = template.growth
    structure = template.structure
    base_temp_c = template.base_temp_c
    if growth is None or structure is None or base_temp_c is None:
        raise CropTemplateError(f"{template.id}.toml 没有完整的物候段，无法构造生长插件")
    return VegetableProfile(
        species=template.species,
        base_temp_c=base_temp_c,
        stages=tuple((stage.name, stage.gdd) for stage in template.stages),
        rue=growth.rue,
        lai_slope=growth.lai_slope,
        lai_max=growth.lai_max,
        senescence_rate=growth.senescence_rate,
        senescence_stages=growth.senescence_stages,
        k_height=structure.k_height,
        max_height_cm=structure.max_height_cm,
    )


TOMATO = _profile(TEMPLATES["tomato"])
CUCUMBER = _profile(TEMPLATES["cucumber"])
LETTUCE = _profile(TEMPLATES["lettuce"])


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
            self.step_plant(plant, env)
        return state

    def step_plant(self, plant, env) -> None:
        """Advance *one* plant one day.

        The per-plant form exists because :mod:`rancho.mosaic` may need a
        different crop for every cell: the dispatcher is what decides which
        plugin each plant goes to, and ``step`` is then just this method run
        over the whole field.
        """
        stage = self._update_phenology(plant, env)
        self._update_canopy(plant, env, stage)
        self._accumulate_biomass(plant)
        self._update_height(plant)

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


def make_plugin(template: CropTemplate) -> type[GreenhouseVegetable]:
    """Register one templated crop as a CropForge plugin.

    CropForge looks plugins up by name, so a class has to exist — but nothing in
    it is crop-specific beyond the profile, so it is built here instead of being
    written out once per crop.  Dropping a new ``<id>.toml`` in
    :mod:`rancho.crops` therefore registers its plugin too.
    """
    namespace = {
        "species": template.species,
        "profile": _profile(template),
        "__doc__": f"{template.label}（{template.species}），参数来自 {template.id}.toml。",
    }
    return register_crop(template.id)(type(template.id.title(), (GreenhouseVegetable,), namespace))


#: Crop key → plugin class, for every template that models its own phenology.
#: Cereals are absent on purpose: CropForge ships those plugins.
PLUGINS: dict[str, type[GreenhouseVegetable]] = {
    key: make_plugin(template) for key, template in TEMPLATES.items() if template.has_phenology
}
