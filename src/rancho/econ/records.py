"""Row shapes for the economics probe.

Kept apart from the agents so that the writers, the probe and the report can
all import them without dragging in Mesa.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DayRecord:
    """One crop's settled day: resources bought, cost paid, growth gained."""

    day: int
    crop: str
    dli_natural: float
    dli_supplemental: float
    dli_effective: float
    temp_mean_c: float
    temp_max_c: float
    temp_passive_c: float
    ks_water: float
    kn_nitrogen: float
    quality: float
    growth_g_m2: float
    biomass_g_m2: float
    yield_kg: float
    kwh_light: float
    kwh_hvac: float
    m3_water: float
    kg_fert: float
    cost_light: float
    cost_hvac: float
    cost_water: float
    cost_fert: float
    cost_overhead: float
    cost_cum: float
    yield_cum_kg: float
    unit_cost: float
    kwh_light_denied: float
    kwh_hvac_denied: float
    m3_water_denied: float
    kg_fert_denied: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class HourRecord:
    """One hour of market state, shared by every crop."""

    hour: int
    day: int
    hour_of_day: int
    temp_out_c: float
    temp_passive_c: float
    radiation_in_mj: float
    tariff: float
    tariff_block: str
    water_price: float
    fert_price: float
    demand_light_kw: float
    served_light_kw: float
    demand_hvac_kw: float
    served_hvac_kw: float
    shadow_thermal: float
    shadow_power: float
    shadow_water: float
    shadow_fert: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
