"""The two agent roles: the house that owns capacity, the crops that bid for it.

The greenhouse is a *market maker*.  Every hour it publishes the state of the
world (outdoor temperature, radiation, tariff, input prices), collects one bid
per crop per resource, and clears each resource in merit order — highest
marginal value first, until the capacity is gone.  The crops are *price takers
with a marginal-value curve*: they do not know the other bids, they only know
their own physiology, their remaining light deficit and the current price.

Three design choices matter for interpreting the output:

1. **Thermal capacity is cleared before electrical capacity.**  Climate control
   prevents irreversible damage; supplemental light only adds growth.  The
   chiller and the heat pump therefore get the first claim on the grid
   connection, and lighting competes for what is left.
2. **Bids are marginal, not average.**  A crop's willingness to pay for one
   more kWh is the derivative of today's marketable growth with respect to that
   resource.  Bids therefore fall as a deficit closes, which is what makes the
   allocation smooth instead of winner-take-all.
3. **The crops are myopic.**  They bid on today's prices and today's state.
   A real grower would arbitrage across days; leaving that out is deliberate,
   because the probe is testing the *coupling*, not the grower's optimal
   control policy.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mesa import Agent

from rancho.econ.growth import GrowthFactory, LightResponse, resolve_growth
from rancho.econ.records import DayRecord, HourRecord
from rancho.econ.spec import (
    COVER_UA_W_M2_K,
    HOURS_PER_DAY,
    LIGHT_HEAT_SHARE,
    MOL_PAR_PER_MJ,
    MOL_PER_KWH,
    PUMP_KWH_PER_M3,
    THERMAL_STEPS,
    WATER_STEPS,
    CropSpec,
    GreenhouseSpec,
)

if TYPE_CHECKING:
    from rancho.econ.model import GreenhouseModel

#: Plant-available nitrogen half-saturation, mirrored from the physics layer so
#: that the economic response curve and the agronomic one cannot diverge.
N_HALF_SATURATION = 28.0
#: Daily net mineralisation from soil organic matter (kg N/ha/day).
N_MINERALISATION = 0.35
#: Nitrogen mass fraction of the fertigation blend.
FERT_N_FRACTION = 0.20

#: Greenhouse air temperature rise per MJ/m²/h of inside radiation (K).  A
#: lumped coefficient standing in for the full energy balance: absorbed solar
#: minus transpiration, divided by the effective loss coefficient.
SOLAR_GAIN_K_PER_MJ = 4.6
SOLAR_GAIN_CAP_K = 9.0
#: Reference COP used only to translate the power budget into a thermal budget.
COP_REFERENCE = 3.2
#: Hour of day at which irrigation and fertigation are cleared.  Once a day,
#: mid-morning, because both are stock decisions rather than hourly ones.
STOCK_HOUR = 6


@dataclass(frozen=True, slots=True)
class Quote:
    """One crop's bid for one resource in one hour."""

    key: str
    quantity: float
    value: float
    #: ``+1`` heating, ``-1`` cooling, ``0`` for resources without a direction.
    direction: float = 0.0


def clear(quotes: Sequence[Quote], capacity: float) -> tuple[dict[str, float], float]:
    """Allocate *capacity* by descending marginal value.

    Returns the served quantity per crop and the shadow price — the marginal
    value of the last unit served, which is zero when capacity is slack.  The
    shadow price is the number that says *how badly* the house was overrun; it
    is reported per hour so the report can show scarcity instead of asserting
    it.
    """
    served = {quote.key: 0.0 for quote in quotes}
    if capacity <= 0.0:
        return served, max((quote.value for quote in quotes if quote.quantity > 0), default=0.0)

    remaining = capacity
    shadow = 0.0
    for quote in sorted(quotes, key=lambda item: (-item.value, item.key)):
        if quote.quantity <= 1e-9:
            continue
        if remaining <= 1e-9:
            break
        taken = min(quote.quantity, remaining)
        served[quote.key] += taken
        remaining -= taken
        shadow = quote.value
    return served, (shadow if remaining <= 1e-6 else 0.0)


def denied_by_crop(
    quotes: Sequence[Quote], served: dict[str, float]
) -> tuple[dict[str, float], dict[str, float]]:
    """Per-crop requested and denied quantities.

    A crop files several bids for one resource (one per step of its bid curve),
    so the request has to be summed across a crop's quotes while ``served`` is
    already cumulative.  Comparing a single step against the total — the
    obvious dict lookup — silently reports zero denial whenever the crop was
    cut after its first step, which is exactly the case the probe measures.
    """
    requested: dict[str, float] = {}
    for quote in quotes:
        requested[quote.key] = requested.get(quote.key, 0.0) + quote.quantity
    denied = {key: max(0.0, quantity - served.get(key, 0.0)) for key, quantity in requested.items()}
    return requested, denied


def cop_cooling(temp_out_c: float) -> float:
    """Chiller COP: falls as the condenser runs hotter."""
    lift = max(5.0, temp_out_c - 2.0)
    return min(5.0, max(2.4, 6.2 - 0.075 * lift))


def cop_heating(temp_out_c: float, temp_set_c: float) -> float:
    """Heat-pump COP: falls as the source-to-sink lift grows."""
    lift = max(10.0, temp_set_c - temp_out_c + 5.0)
    return min(4.5, max(1.7, 6.0 - 0.09 * lift))


class CropAgent(Agent):
    """A bed of one crop with a marginal-value bid curve and a cost ledger."""

    def __init__(
        self,
        model: GreenhouseModel,
        spec: CropSpec,
        index: int,
        *,
        growth: str | GrowthFactory = "cropforge",
    ) -> None:
        super().__init__(model)
        self.spec = spec
        self.index = index
        self.growth: LightResponse = resolve_growth(growth, spec)

        # --- slow state -------------------------------------------------
        self.moisture_pct = spec.initial_moisture_pct
        self.n_kg_ha = spec.initial_n_kg_ha
        self.biomass_g_m2 = 0.0
        self.yield_kg = 0.0
        self.damage = 0.0
        self.quality = 1.0

        # --- hourly state -----------------------------------------------
        self.zone_temp_c = 0.0
        self.hvac_kw = 0.0
        self.hvac_kw_electric = 0.0
        self.light_kw = 0.0
        self.thermal_sign = 0.0
        self.thermal_setpoint_c = spec.setpoint(12)
        self.dli_natural_day = 0.0
        self.dli_supp_day = 0.0
        self.temp_factor_sum = 0.0
        self.temp_sum = 0.0
        self.temp_max = -99.0
        self.temp_samples = 0

        # --- daily resource ledger --------------------------------------
        self.kwh_light_day = 0.0
        self.kwh_hvac_day = 0.0
        self.m3_water_day = 0.0
        self.kg_fert_day = 0.0
        self.fert_n_day = 0.0
        self.kwh_light_denied_day = 0.0
        self.kwh_hvac_denied_day = 0.0
        self.m3_water_denied_day = 0.0
        self.kg_fert_denied_day = 0.0
        self.cost_light_day = 0.0
        self.cost_hvac_day = 0.0
        self.cost_water_day = 0.0
        self.cost_fert_day = 0.0
        self.cost_overhead_day = 0.0

        # --- cumulative ledger -------------------------------------------
        self.kwh_light = 0.0
        self.kwh_hvac = 0.0
        self.kwh_hvac_thermal = 0.0
        self.m3_water = 0.0
        self.kg_fert = 0.0
        self.kwh_light_denied = 0.0
        self.kwh_hvac_denied = 0.0
        self.m3_water_denied = 0.0
        self.kg_fert_denied = 0.0
        self.cost_light = 0.0
        self.cost_hvac = 0.0
        self.cost_water = 0.0
        self.cost_fert = 0.0
        self.cost_overhead = 0.0

    # ------------------------------------------------------------------
    # Factors
    # ------------------------------------------------------------------

    @property
    def cost_total(self) -> float:
        return (
            self.cost_light + self.cost_hvac + self.cost_water + self.cost_fert + self.cost_overhead
        )

    @property
    def unit_cost(self) -> float:
        """Cumulative yuan per kg of marketable growth accrued so far."""
        return self.cost_total / self.yield_kg if self.yield_kg > 1e-9 else 0.0

    def water_factor(self) -> float:
        """FAO-56 stress coefficient from root-zone depletion."""
        return self._water_factor_at(self.moisture_pct)

    def nitrogen_factor(self) -> float:
        """Mitscherlich response, floored so a starved crop still transpires."""
        return min(1.0, max(0.18, 1.0 - math.exp(-self.n_kg_ha / N_HALF_SATURATION)))

    def _day_temp_factor(self) -> float:
        if self.temp_samples == 0:
            return self.spec.temp_factor(self.spec.setpoint(12))
        return self.temp_factor_sum / self.temp_samples

    # ------------------------------------------------------------------
    # Bids
    # ------------------------------------------------------------------

    def quotes_thermal(
        self,
        temp_passive_c: float,
        hour_of_day: int,
        tariff: float,
        temp_out_c: float,
    ) -> list[Quote]:
        """Bid curve for the heat or cold needed to reach this crop's setpoint.

        The request is split into equal-energy steps, each valued across the
        temperature interval it buys.  Because the response is concave, the
        first kelvin is worth more than the last, and the clearing price stops
        somewhere *inside* a crop's request instead of at one edge of it.

        Each step is also tested against the electricity it would take to
        deliver it, so a crop stops short of its setpoint when the remaining
        kelvin costs more than the growth it buys.  That is what makes the
        setpoint economic rather than a fixed control target, and it is why a
        crop that loses the auction ends up *less* intensive rather than simply
        hotter at the same cost.
        """
        spec = self.spec
        setpoint = spec.setpoint(hour_of_day)
        self.thermal_setpoint_c = setpoint
        delta = setpoint - temp_passive_c
        self.thermal_sign = 1.0 if delta > 0.0 else -1.0

        if abs(delta) < 0.15:
            self.thermal_sign = 0.0
            return []

        dli = self.dli_natural_day + self.dli_supp_day
        # Yuan value of one full unit of the temperature factor for one day.
        # The production term comes from the growth backend, so a crop with a
        # closed canopy pays more for the last kelvin than a seedling does.
        daily_value = spec.price_yuan_kg * self.growth.production(dli) * spec.area_m2 / 1000.0

        cop = cop_heating(temp_out_c, setpoint) if delta > 0.0 else cop_cooling(temp_out_c)
        reservation = tariff / cop

        step_delta = delta / THERMAL_STEPS
        step_kw = COVER_UA_W_M2_K * spec.area_m2 * abs(step_delta) / 1000.0
        if step_kw <= 1e-3:
            return []

        # Loop invariants: the ledger does not move while a crop builds a bid.
        quality = self.quality
        horizon = self.season_days_left()

        quotes: list[Quote] = []
        for index in range(THERMAL_STEPS):
            start = temp_passive_c + step_delta * index
            end = start + step_delta
            growth_gain = spec.temp_factor(end) - spec.temp_factor(start)
            # Above the critical temperature the bid buys damage avoidance
            # rather than growth; the growth term is already zero out there.
            damage_avoided = spec.damage_rate(start) - spec.damage_rate(end)
            value = growth_gain * daily_value / HOURS_PER_DAY
            # Quality damage is irreversible, so an hour of it costs the rest
            # of the season's production, not just today's.  Pricing it against
            # one day — the phase-1 convention — makes a low-value bed give up
            # on cooling and then spiral: less cooling, more damage, less
            # production, and therefore a still smaller bid.
            value += damage_avoided * daily_value * quality * horizon
            unit_value = value / step_kw
            if unit_value > reservation:
                quotes.append(Quote(spec.key, step_kw, unit_value, self.thermal_sign))
        return quotes

    def quote_light(self, natural_remaining_mol: float, tariff: float) -> Quote | None:
        """Bid for supplemental light up to the fixture limit, if it pays."""
        spec = self.spec
        expected = self.dli_natural_day + natural_remaining_mol + self.dli_supp_day
        deficit = spec.dli_target - expected
        if deficit <= 0.4:
            return None

        stress = (
            self._day_temp_factor() * self.water_factor() * self.nitrogen_factor() * self.quality
        )
        value = self.growth.marginal(expected) * MOL_PER_KWH / 1000.0 * spec.price_yuan_kg * stress
        if value <= tariff:
            return None

        fixture_kw = spec.light_w_m2 * spec.area_m2 / 1000.0
        needed_kw = deficit * spec.area_m2 / MOL_PER_KWH
        return Quote(spec.key, min(fixture_kw, needed_kw), value)

    def quotes_water(
        self,
        et0_mm: float,
        dli_expected: float,
    ) -> list[Quote]:
        """Bid curve for irrigation water, valued by the stress it relieves.

        Irrigation is a hard operational constraint as well — a grower does not
        let a bed wilt to save 4 元/m³ — so the whole requirement is quoted and
        the well's yield decides how much is served.
        """
        spec = self.spec
        need_mm = max(0.0, spec.kc_mid * et0_mm - self.m3_water_day * 1000.0 / spec.area_m2)
        if need_mm <= 0.2:
            return []

        production = self.growth.production(dli_expected)
        floor = self._day_temp_factor() * self.nitrogen_factor() * self.quality
        step_mm = need_mm / WATER_STEPS
        step_m3 = step_mm * spec.area_m2 / 1000.0
        moisture = self.moisture_pct

        quotes: list[Quote] = []
        for _ in range(WATER_STEPS):
            before = self._water_factor_at(moisture)
            moisture += step_mm / spec.root_zone_mm * 100.0
            gain = max(0.0, self._water_factor_at(moisture) - before)
            growth_gain = gain * production * floor
            value = growth_gain / 1000.0 * spec.price_yuan_kg * spec.area_m2
            if value > 0.0:
                quotes.append(Quote(spec.key, step_m3, value / step_m3))
        return quotes

    def quote_fert(self, dli_expected: float, fert_price: float) -> Quote | None:
        """Bid for fertigation stock, stopping where marginal value meets price."""
        spec = self.spec
        target_kg = spec.fert_g_m2_day * spec.area_m2 / 1000.0 - self.kg_fert_day
        if target_kg <= 1e-4:
            return None

        n_per_kg = 1000.0 / spec.area_m2 * 10.0 * FERT_N_FRACTION
        before = self.nitrogen_factor()
        after = min(1.0, 1.0 - math.exp(-(self.n_kg_ha + n_per_kg) / N_HALF_SATURATION))
        gain = max(0.0, after - before)

        growth_gain = gain * self.growth.production(dli_expected)
        growth_gain *= self._day_temp_factor() * self.water_factor() * self.quality
        value = growth_gain / 1000.0 * spec.price_yuan_kg * spec.area_m2
        if value <= fert_price:
            return None
        return Quote(spec.key, target_kg, value)

    def season_days_left(self) -> float:
        """Days of production still ahead, floored at one.

        The horizon a quality loss is priced against: damage taken today
        lowers every remaining day's yield, not only this one's.
        """
        horizon = self.model.forcing.days
        elapsed = self.model.hour // HOURS_PER_DAY
        return float(max(1, horizon - elapsed))

    def _water_factor_at(self, moisture_pct: float) -> float:
        span = max(
            1e-6,
            self.spec.depletion_fraction
            * (self.spec.field_capacity_pct - self.spec.wilting_point_pct),
        )
        return min(1.0, max(0.0, (moisture_pct - self.spec.wilting_point_pct) / span))

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------

    def observe_natural(self, mol: float) -> None:
        self.dli_natural_day += mol

    def apply_thermal(self, thermal_kw: float, tariff: float, temp_out_c: float) -> None:
        """Book delivered heat/cold, its electricity and the resulting zone temperature."""
        if thermal_kw <= 1e-9:
            self.hvac_kw = 0.0
            self.hvac_kw_electric = 0.0
            return

        if self.thermal_sign >= 0.0:
            cop = cop_heating(temp_out_c, self.thermal_setpoint_c)
            self.hvac_kw = thermal_kw
        else:
            cop = cop_cooling(temp_out_c)
            self.hvac_kw = -thermal_kw

        kwh = thermal_kw / cop
        self.hvac_kw_electric = kwh
        self.kwh_hvac_thermal += thermal_kw
        charge = kwh * tariff
        self.kwh_hvac_day += kwh
        self.kwh_hvac += kwh
        self.cost_hvac += charge
        self.cost_hvac_day += charge

    def apply_light(self, light_kw: float, tariff: float) -> None:
        """Book delivered lighting power and the photons it adds to the canopy."""
        spec = self.spec
        self.light_kw = max(0.0, light_kw)
        if self.light_kw <= 1e-9:
            return
        kwh = self.light_kw
        mol = kwh * MOL_PER_KWH / spec.area_m2
        self.dli_supp_day += mol
        self.kwh_light_day += kwh
        self.kwh_light += kwh
        self.cost_light += kwh * tariff
        self.cost_light_day += kwh * tariff

    def apply_water(self, m3: float, price_m3: float, tariff: float) -> None:
        """Book water, its pumping energy and the root-zone refill."""
        spec = self.spec
        if m3 <= 1e-9:
            return
        mm = m3 * 1000.0 / spec.area_m2
        self.moisture_pct = min(
            spec.field_capacity_pct,
            self.moisture_pct + mm / spec.root_zone_mm * 100.0,
        )
        self.m3_water_day += m3
        self.m3_water += m3
        water_charge = m3 * (price_m3 + PUMP_KWH_PER_M3 * tariff)
        self.cost_water += water_charge
        self.cost_water_day += water_charge

    def apply_fert(self, kg: float, price_kg: float) -> None:
        spec = self.spec
        if kg <= 1e-9:
            return
        self.fert_n_day += kg * 1000.0 / spec.area_m2 * 10.0 * FERT_N_FRACTION
        self.kg_fert_day += kg
        self.kg_fert += kg
        self.cost_fert += kg * price_kg
        self.cost_fert_day += kg * price_kg

    def observe_temp(self, temp_c: float) -> None:
        """Record the zone temperature reached this hour and the damage it did."""
        self.zone_temp_c = temp_c
        self.temp_sum += temp_c
        self.temp_max = max(self.temp_max, temp_c)
        self.temp_samples += 1
        self.temp_factor_sum += self.spec.temp_factor(temp_c)
        self.damage += self.spec.damage_rate(temp_c)
        self.quality = math.exp(-self.damage)

    def charge_overhead(self, yuan: float) -> None:
        self.cost_overhead += yuan
        self.cost_overhead_day += yuan

    # ------------------------------------------------------------------
    # Daily settlement
    # ------------------------------------------------------------------

    def settle_day(self, day: int, et0_mm: float, temp_passive_c: float) -> DayRecord:
        """Turn the day's resources into growth, close the balances, emit a row."""
        spec = self.spec
        dli = self.dli_natural_day + self.dli_supp_day
        ks = self.water_factor()
        kn = self.nitrogen_factor()

        growth = self.growth.production(dli) * self._day_temp_factor() * ks * kn
        growth *= self.quality
        # The canopy the next day's bids are priced against; a no-op for the
        # analytic backend.
        self.growth.settle(dli, self.temp_sum / max(1, self.temp_samples))
        self.biomass_g_m2 += growth
        self.yield_kg += growth * spec.area_m2 / 1000.0

        # Water balance: extraction after the day's stress has been applied.
        etc = spec.kc_mid * et0_mm * ks
        self.moisture_pct = max(
            spec.wilting_point_pct * 0.55,
            self.moisture_pct - etc / spec.root_zone_mm * 100.0,
        )

        uptake = growth * spec.dry_matter_fraction * 10.0 * spec.n_concentration
        self.n_kg_ha = max(
            1.5,
            self.n_kg_ha + N_MINERALISATION + self.fert_n_day - uptake,
        )

        record = DayRecord(
            day=day + 1,
            crop=spec.key,
            dli_natural=round(self.dli_natural_day, 2),
            dli_supplemental=round(self.dli_supp_day, 2),
            dli_effective=round(dli, 2),
            temp_mean_c=round(self.temp_sum / max(1, self.temp_samples), 2),
            temp_max_c=round(self.temp_max, 2),
            temp_passive_c=round(temp_passive_c, 2),
            ks_water=round(ks, 4),
            kn_nitrogen=round(kn, 4),
            quality=round(self.quality, 4),
            growth_g_m2=round(growth, 3),
            biomass_g_m2=round(self.biomass_g_m2, 2),
            yield_kg=round(self.yield_kg, 3),
            kwh_light=round(self.kwh_light_day, 3),
            kwh_hvac=round(self.kwh_hvac_day, 3),
            m3_water=round(self.m3_water_day, 4),
            kg_fert=round(self.kg_fert_day, 4),
            cost_light=round(self.cost_light_day, 3),
            cost_hvac=round(self.cost_hvac_day, 3),
            cost_water=round(self.cost_water_day, 3),
            cost_fert=round(self.cost_fert_day, 3),
            cost_overhead=round(self.cost_overhead_day, 3),
            cost_cum=round(self.cost_total, 3),
            yield_cum_kg=round(self.yield_kg, 3),
            unit_cost=round(self.unit_cost, 4),
            kwh_light_denied=round(self.kwh_light_denied_day, 3),
            kwh_hvac_denied=round(self.kwh_hvac_denied_day, 3),
            m3_water_denied=round(self.m3_water_denied_day, 4),
            kg_fert_denied=round(self.kg_fert_denied_day, 4),
        )
        self.reset_day()
        return record

    def reset_day(self) -> None:
        self.dli_natural_day = 0.0
        self.dli_supp_day = 0.0
        self.temp_factor_sum = 0.0
        self.temp_sum = 0.0
        self.temp_max = -99.0
        self.temp_samples = 0
        self.kwh_light_day = 0.0
        self.kwh_hvac_day = 0.0
        self.m3_water_day = 0.0
        self.kg_fert_day = 0.0
        self.fert_n_day = 0.0
        self.kwh_light_denied_day = 0.0
        self.kwh_hvac_denied_day = 0.0
        self.m3_water_denied_day = 0.0
        self.kg_fert_denied_day = 0.0
        self.cost_light_day = 0.0
        self.cost_hvac_day = 0.0
        self.cost_water_day = 0.0
        self.cost_fert_day = 0.0
        self.cost_overhead_day = 0.0


class GreenhouseAgent(Agent):
    """The house: owns capacity, runs the auctions, keeps the hourly log."""

    def __init__(
        self,
        model: GreenhouseModel,
        spec: GreenhouseSpec,
        crops: Iterable[CropAgent],
        *,
        cap_scale: float = 1.0,
    ) -> None:
        super().__init__(model)
        self.spec = spec
        self.crops: tuple[CropAgent, ...] = tuple(crops)
        self.cap_scale = cap_scale
        self.power_cap_kw = spec.power_cap_kw * cap_scale
        self.thermal_cap_kw = spec.thermal_cap_kw * cap_scale
        self.water_cap_m3 = spec.water_cap_m3_day * cap_scale
        self.fert_cap_kg = spec.fert_cap_kg_day * cap_scale
        self.total_area_m2 = sum(crop.spec.area_m2 for crop in self.crops)
        self.hours: list[HourRecord] = []
        self._passive_sum = 0.0
        self.dli_left = 0.0

    # ------------------------------------------------------------------
    # Mesa step — one hour of market activity
    # ------------------------------------------------------------------

    def step(self) -> None:
        model = self.model
        hour = model.hour
        day, hour_of_day = divmod(hour, HOURS_PER_DAY)
        forcing = model.forcing

        temp_out = forcing.temp_out[hour]
        radiation = forcing.radiation_in[hour]
        tariff = forcing.tariff_price[hour]
        passive = temp_out + min(SOLAR_GAIN_CAP_K, SOLAR_GAIN_K_PER_MJ * radiation)
        natural_mol = radiation * MOL_PAR_PER_MJ

        # Daylight still to come today, carried rather than re-summed every
        # hour: it is the day's DLI minus what has already arrived.
        if hour_of_day == 0:
            self.dli_left = forcing.dli(day)
        self.dli_left -= natural_mol

        for crop in self.crops:
            crop.observe_natural(natural_mol)

        thermal_served, shadow_thermal, thermal_demand = self._clear_thermal(
            passive, hour_of_day, tariff, temp_out
        )
        light_served, shadow_power, light_demand = self._clear_light(self.dli_left, tariff)

        for crop in self.crops:
            crop.apply_thermal(thermal_served.get(crop.spec.key, 0.0), tariff, temp_out)
            crop.apply_light(light_served.get(crop.spec.key, 0.0), tariff)
            zone = passive + self._zone_offset(crop)
            crop.observe_temp(zone)
            crop.charge_overhead(
                self.spec.standing_kw * tariff * crop.spec.area_m2 / self.total_area_m2
            )
            crop.charge_overhead(self.spec.overhead_yuan_m2_day * crop.spec.area_m2 / HOURS_PER_DAY)

        water_shadow = fert_shadow = 0.0
        if hour_of_day == STOCK_HOUR:
            dli_today = forcing.dli(day)
            water_shadow = self._clear_water(
                self._et0_mm(day), dli_today, tariff, forcing.water_price[hour]
            )
            fert_shadow = self._clear_fert(dli_today, forcing.fert_price[hour])

        self._passive_sum += passive
        # Kept at full precision: the only consumer is the report, which
        # formats on the way out, and the hourly log stays exact for anything
        # that later reads it back.
        self.hours.append(
            HourRecord(
                hour=hour,
                day=day + 1,
                hour_of_day=hour_of_day,
                temp_out_c=temp_out,
                temp_passive_c=passive,
                radiation_in_mj=radiation,
                tariff=tariff,
                tariff_block=forcing.tariff_block[hour],
                water_price=forcing.water_price[hour],
                fert_price=forcing.fert_price[hour],
                demand_light_kw=light_demand,
                served_light_kw=sum(light_served.values()),
                demand_hvac_kw=thermal_demand,
                served_hvac_kw=sum(thermal_served.values()),
                shadow_thermal=shadow_thermal,
                shadow_power=shadow_power,
                shadow_water=water_shadow,
                shadow_fert=fert_shadow,
            )
        )

        if hour_of_day == HOURS_PER_DAY - 1:
            self._settle_day(day, self._et0_mm(day), self._passive_sum / HOURS_PER_DAY)
            self._passive_sum = 0.0

    # ------------------------------------------------------------------
    # Clearing
    # ------------------------------------------------------------------

    def _clear_thermal(
        self,
        passive_c: float,
        hour_of_day: int,
        tariff: float,
        temp_out_c: float,
    ) -> tuple[dict[str, float], float, float]:
        """Climate control gets first claim on the power budget."""
        quotes = [
            quote
            for crop in self.crops
            for quote in crop.quotes_thermal(passive_c, hour_of_day, tariff, temp_out_c)
        ]
        if not quotes:
            return {}, 0.0, 0.0

        usable_power = max(0.0, self.power_cap_kw - self.spec.standing_kw)
        effective = min(self.thermal_cap_kw, usable_power * COP_REFERENCE)
        served, shadow = clear(quotes, effective)

        requested, denied = denied_by_crop(quotes, served)
        for crop in self.crops:
            short = denied.get(crop.spec.key, 0.0)
            if short > 0.0:
                crop.kwh_hvac_denied_day += short
                crop.kwh_hvac_denied += short
        return served, shadow, sum(requested.values())

    def _clear_light(
        self, remaining_mol: float, tariff: float
    ) -> tuple[dict[str, float], float, float]:
        """Supplemental lighting competes for what climate control leaves over."""
        quotes = [
            quote
            for crop in self.crops
            if (quote := crop.quote_light(remaining_mol, tariff)) is not None
        ]
        if not quotes:
            return {}, 0.0, 0.0

        hvac_kw = sum(crop.hvac_kw_electric for crop in self.crops)
        residual = max(0.0, self.power_cap_kw - self.spec.standing_kw - hvac_kw)
        served, shadow = clear(quotes, residual)

        requested, denied = denied_by_crop(quotes, served)
        for crop in self.crops:
            short = denied.get(crop.spec.key, 0.0)
            if short > 0.0:
                crop.kwh_light_denied_day += short
                crop.kwh_light_denied += short
        return served, shadow, sum(requested.values())

    def _clear_water(
        self, et0_mm: float, dli_expected: float, tariff: float, price_m3: float
    ) -> float:
        quotes = [quote for crop in self.crops for quote in crop.quotes_water(et0_mm, dli_expected)]
        if not quotes:
            return 0.0
        served, shadow = clear(quotes, self.water_cap_m3)
        _requested, denied = denied_by_crop(quotes, served)
        for crop in self.crops:
            key = crop.spec.key
            short = denied.get(key, 0.0)
            crop.m3_water_denied_day += short
            crop.m3_water_denied += short
            crop.apply_water(served.get(key, 0.0), price_m3, tariff)
        return shadow

    def _clear_fert(self, dli_expected: float, price_kg: float) -> float:
        quotes = [
            quote
            for crop in self.crops
            if (quote := crop.quote_fert(dli_expected, price_kg)) is not None
        ]
        if not quotes:
            return 0.0
        served, shadow = clear(quotes, self.fert_cap_kg)
        _requested, denied = denied_by_crop(quotes, served)
        for crop in self.crops:
            key = crop.spec.key
            short = denied.get(key, 0.0)
            crop.kg_fert_denied_day += short
            crop.kg_fert_denied += short
            crop.apply_fert(served.get(key, 0.0), price_kg)
        return shadow

    # ------------------------------------------------------------------
    # Physics helpers
    # ------------------------------------------------------------------

    def _et0_mm(self, day: int) -> float:
        """Reference evapotranspiration for a day, mm.

        Hargreaves-lite: radiation sets the bulk, a warm day adds a little.
        """
        forcing = self.model.forcing
        return 0.28 * forcing.day_radiation(day) + 0.06 * max(0.0, forcing.mean_temp(day) - 15.0)

    def _zone_offset(self, crop: CropAgent) -> float:
        """Temperature offset from the delivered heat (or cold) and the lights."""
        capacity_kw_per_k = COVER_UA_W_M2_K * crop.spec.area_m2 / 1000.0
        load = crop.hvac_kw + LIGHT_HEAT_SHARE * crop.light_kw
        return load / capacity_kw_per_k

    def _settle_day(self, day: int, et0_mm: float, passive_mean: float) -> None:
        for crop in self.crops:
            self.model.records.append(crop.settle_day(day, et0_mm, passive_mean))
