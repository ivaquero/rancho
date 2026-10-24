"""Synthetic arms for testing the verdict without running the model.

The verdict only reads snapshots, so the tests build them by hand.  That keeps
a threshold test at a fixed distance from the threshold instead of at whatever
distance a 28-day run happens to land on, and it lets one case isolate the
null check while another isolates the pressure floor.
"""

from __future__ import annotations

from collections.abc import Sequence

from rancho.econ.forcing import Forcing, build_forcing
from rancho.econ.model import ArmResult, CropSnapshot
from rancho.econ.spec import GREENHOUSE, SITES, TARIFFS


def one_day_forcing() -> Forcing:
    """A real forcing block, one day long — enough to make an arm honest."""
    return build_forcing(
        SITES["tropical"],
        TARIFFS["sv_horaria"],
        days=1,
        seed=1,
        water_price_base=GREENHOUSE.water_price_yuan_m3,
        fert_price_base=GREENHOUSE.fert_price_yuan_kg,
    )


def make_snapshot(
    key: str,
    *,
    yield_kg: float,
    cost_total: float,
    thermal_kwh: float = 0.0,
    light_kwh: float = 0.0,
    denied_hvac: float = 0.0,
    denied_light: float = 0.0,
    price: float = 12.0,
) -> CropSnapshot:
    """One crop's end-of-arm totals, with only the fields the probe reads set."""
    revenue = yield_kg * price
    return CropSnapshot(
        key=key,
        label=key.capitalize(),
        area_m2=100.0,
        price_yuan_kg=price,
        yield_kg=yield_kg,
        biomass_g_m2=yield_kg * 10.0,
        quality=1.0,
        moisture_pct=30.0,
        n_kg_ha=30.0,
        cost_light=0.0,
        cost_hvac=0.0,
        cost_water=0.0,
        cost_fert=0.0,
        cost_overhead=0.0,
        cost_total=cost_total,
        unit_cost=cost_total / yield_kg if yield_kg > 1e-9 else 0.0,
        kwh_light=light_kwh,
        kwh_hvac=0.0,
        kwh_hvac_thermal=thermal_kwh,
        m3_water=0.0,
        kg_fert=0.0,
        kwh_light_denied=denied_light,
        kwh_hvac_denied=denied_hvac,
        m3_water_denied=0.0,
        kg_fert_denied=0.0,
        revenue_yuan=revenue,
        margin_yuan=revenue - cost_total,
    )


def make_arm(
    name: str,
    crops: Sequence[CropSnapshot],
    *,
    forcing: Forcing | None = None,
) -> ArmResult:
    """Wrap snapshots as an arm; records and hours stay empty on purpose."""
    return ArmResult(
        arm=name,
        cap_scale=1.0,
        days=1,
        forcing=forcing if forcing is not None else one_day_forcing(),
        records=(),
        hours=(),
        crops=tuple(crops),
    )
