"""Mesa model assembly and the single-arm runner.

One arm is one run of the model under one set of capacities.  Running the same
forcing under three capacity settings — unlimited, real, and deliberately
generous — is what separates the *mechanism* from the *noise*:

===== =============== ==========================================================
arm   capacity        what it answers
===== =============== ==========================================================
solo  none (×10⁴)     what each crop would buy if it had the house to itself
pooled real           what three crops get when they share the connection
null  generous (×10³) does the allocator itself bias the result?
===== =============== ==========================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from mesa import Model

from rancho.econ.agents import CropAgent, GreenhouseAgent
from rancho.econ.forcing import Forcing, build_forcing
from rancho.econ.growth import GrowthFactory
from rancho.econ.records import DayRecord, HourRecord
from rancho.econ.spec import (
    GREENHOUSE,
    HOURS_PER_DAY,
    CropSpec,
    GreenhouseSpec,
    SiteProfile,
    TariffSpec,
)

#: Capacity multipliers for the two reference arms.
SOLO_SCALE = 1.0e4
NULL_SCALE = 1.0e3


@dataclass(frozen=True, slots=True)
class CropSnapshot:
    """End-of-arm totals for one crop."""

    key: str
    label: str
    area_m2: float
    price_yuan_kg: float
    yield_kg: float
    biomass_g_m2: float
    quality: float
    moisture_pct: float
    n_kg_ha: float
    cost_light: float
    cost_hvac: float
    cost_water: float
    cost_fert: float
    cost_overhead: float
    cost_total: float
    unit_cost: float
    kwh_light: float
    kwh_hvac: float
    kwh_hvac_thermal: float
    m3_water: float
    kg_fert: float
    kwh_light_denied: float
    kwh_hvac_denied: float
    m3_water_denied: float
    kg_fert_denied: float
    revenue_yuan: float
    margin_yuan: float
    #: Leaf area index at the end of the arm (0.0 for the analytic backend,
    #: which carries no canopy state).
    lai: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "area_m2": self.area_m2,
            "price_yuan_kg": self.price_yuan_kg,
            "yield_kg": round(self.yield_kg, 3),
            "biomass_g_m2": round(self.biomass_g_m2, 2),
            "quality": round(self.quality, 4),
            "moisture_pct": round(self.moisture_pct, 2),
            "n_kg_ha": round(self.n_kg_ha, 2),
            "cost": {
                "light": round(self.cost_light, 3),
                "hvac": round(self.cost_hvac, 3),
                "water": round(self.cost_water, 3),
                "fert": round(self.cost_fert, 3),
                "overhead": round(self.cost_overhead, 3),
                "total": round(self.cost_total, 3),
            },
            "unit_cost": round(self.unit_cost, 4),
            "resources": {
                "kwh_light": round(self.kwh_light, 3),
                "kwh_hvac": round(self.kwh_hvac, 3),
                "kwh_hvac_thermal": round(self.kwh_hvac_thermal, 3),
                "m3_water": round(self.m3_water, 4),
                "kg_fert": round(self.kg_fert, 4),
            },
            "denied": {
                "kwh_light": round(self.kwh_light_denied, 3),
                "kwh_hvac": round(self.kwh_hvac_denied, 3),
                "m3_water": round(self.m3_water_denied, 4),
                "kg_fert": round(self.kg_fert_denied, 4),
            },
            "revenue_yuan": round(self.revenue_yuan, 3),
            "margin_yuan": round(self.margin_yuan, 3),
            "lai": round(self.lai, 3),
        }


@dataclass(frozen=True, slots=True)
class ArmResult:
    """Everything one arm produced, plus its forcing, for later comparison."""

    arm: str
    cap_scale: float
    days: int
    forcing: Forcing
    records: tuple[DayRecord, ...]
    hours: tuple[HourRecord, ...]
    crops: tuple[CropSnapshot, ...]

    def by_crop(self) -> dict[str, CropSnapshot]:
        return {crop.key: crop for crop in self.crops}

    def total_cost(self) -> float:
        return sum(crop.cost_total for crop in self.crops)

    def total_yield(self) -> float:
        return sum(crop.yield_kg for crop in self.crops)


class GreenhouseModel(Model):
    """Mesa model: one greenhouse agent, one agent per crop bed, hourly steps."""

    def __init__(
        self,
        *,
        forcing: Forcing,
        crops: tuple[CropSpec, ...],
        greenhouse: GreenhouseSpec = GREENHOUSE,
        seed: int,
        cap_scale: float = 1.0,
        growth: str | GrowthFactory = "cropforge",
    ) -> None:
        super().__init__(rng=np.random.default_rng(seed))
        self.forcing = forcing
        self.hour = 0
        self.records: list[DayRecord] = []
        agents = [CropAgent(self, spec, index, growth=growth) for index, spec in enumerate(crops)]
        self.crop_agents: tuple[CropAgent, ...] = tuple(agents)
        self.greenhouse = GreenhouseAgent(self, greenhouse, agents, cap_scale=cap_scale)

    def step(self) -> None:  # ty: ignore[invalid-method-override]
        """Advance the market by one hour, then close the day if it is over.

        Mesa types ``Model.step`` through a descriptor that ty reads as a free
        function, so the override looks unsound to the checker while being the
        documented contract.  ``run_for`` calls this once per hour.
        """
        self.greenhouse.step()
        self.hour += 1


def run_arm(
    *,
    site: SiteProfile,
    tariff: TariffSpec,
    crops: tuple[CropSpec, ...],
    days: int,
    seed: int,
    arm: str,
    cap_scale: float = 1.0,
    greenhouse: GreenhouseSpec = GREENHOUSE,
    growth: str | GrowthFactory = "cropforge",
) -> ArmResult:
    """Build the forcing, run the model, and snapshot the result."""
    forcing = build_forcing(
        site,
        tariff,
        days=days,
        seed=seed,
        water_price_base=greenhouse.water_price_yuan_m3,
        fert_price_base=greenhouse.fert_price_yuan_kg,
    )
    model = GreenhouseModel(
        forcing=forcing,
        crops=crops,
        greenhouse=greenhouse,
        seed=seed,
        cap_scale=cap_scale,
        growth=growth,
    )
    model.run_for(days * HOURS_PER_DAY)

    snapshots = tuple(_snapshot(agent) for agent in model.crop_agents)
    return ArmResult(
        arm=arm,
        cap_scale=cap_scale,
        days=days,
        forcing=forcing,
        records=tuple(model.records),
        hours=tuple(model.greenhouse.hours),
        crops=snapshots,
    )


def _snapshot(agent: CropAgent) -> CropSnapshot:
    spec = agent.spec
    revenue = agent.yield_kg * spec.price_yuan_kg
    return CropSnapshot(
        key=spec.key,
        label=spec.label,
        area_m2=spec.area_m2,
        price_yuan_kg=spec.price_yuan_kg,
        yield_kg=agent.yield_kg,
        biomass_g_m2=agent.biomass_g_m2,
        quality=agent.quality,
        moisture_pct=agent.moisture_pct,
        n_kg_ha=agent.n_kg_ha,
        cost_light=agent.cost_light,
        cost_hvac=agent.cost_hvac,
        cost_water=agent.cost_water,
        cost_fert=agent.cost_fert,
        cost_overhead=agent.cost_overhead,
        cost_total=agent.cost_total,
        unit_cost=agent.unit_cost,
        kwh_light=agent.kwh_light,
        kwh_hvac=agent.kwh_hvac,
        kwh_hvac_thermal=agent.kwh_hvac_thermal,
        m3_water=agent.m3_water,
        kg_fert=agent.kg_fert,
        kwh_light_denied=agent.kwh_light_denied,
        kwh_hvac_denied=agent.kwh_hvac_denied,
        m3_water_denied=agent.m3_water_denied,
        kg_fert_denied=agent.kg_fert_denied,
        revenue_yuan=revenue,
        margin_yuan=revenue - agent.cost_total,
        lai=float(getattr(agent.growth, "lai", 0.0)),
    )
