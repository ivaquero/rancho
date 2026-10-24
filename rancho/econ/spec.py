"""Parameter objects for the greenhouse economics probe.

Every number that a grower could argue about lives here, in one place, with its
unit in the field name.  Nothing about a crop is duplicated here: the water
coefficients come from :data:`rancho.agronomy.CROP_WATER`, the product
description from :data:`rancho.scenario.CROP_PROFILES`, and the price, light
response and temperature tolerance from the crop template's ``[market]`` table.
What is left in this module is the bed — its area and its starting soil.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from rancho.agronomy import CROP_WATER
from rancho.crops import TEMPLATES
from rancho.scenario import CROP_PROFILES

#: Hours in a model day.  The probe steps hourly because a flat daily tariff
#: would hide the peak/valley arbitrage that drives most of the decision.
HOURS_PER_DAY: Final = 24

#: Share of global radiation that is photosynthetically active, and the photon
#: content of one MJ of PAR.  Together they turn an MJ of sunlight into moles.
PAR_FRACTION: Final = 0.45
MOL_PER_MJ_PAR: Final = 4.57
#: The two above combined, which is what every caller actually wants.  Applying
#: only one of the factors is a 55 % error that reads as a plausible number, so
#: the conversion is exposed once and used everywhere.
MOL_PAR_PER_MJ: Final = PAR_FRACTION * MOL_PER_MJ_PAR

#: Photon yield of the LED fixture (µmol/J) and the resulting mol per kWh.
LED_UMOL_PER_J: Final = 3.0
MOL_PER_KWH: Final = LED_UMOL_PER_J * 3.6

#: Share of lighting electricity that ends up as sensible heat in the canopy
#: layer.  The rest leaves as radiation that the canopy does not absorb.
LIGHT_HEAT_SHARE: Final = 0.55

#: Specific electric demand of the irrigation pump (kWh per m³ lifted and pressurised).
PUMP_KWH_PER_M3: Final = 0.35

#: Overall heat-transfer coefficient of the covered structure (W per m² per K).
#: A plastic house with insect netting sits near 6; a glasshouse near 8.  The
#: value used here includes the ventilation the net allows, which is why it is
#: higher than a sealed-house figure.
COVER_UA_W_M2_K: Final = 9.0

#: Step count of the bid curves.  A crop does not value the tenth kelvin of
#: cooling as much as the first, so its request is split into equal energy
#: steps with decreasing marginal value.  Three is enough to keep the clearing
#: smooth without turning each auction into a linear program.
THERMAL_STEPS: Final = 3
WATER_STEPS: Final = 3


#: The currency every amount is booked in.  Sale prices (``price_yuan_kg``),
#: water and fertiliser are all quoted in yuan, so electricity has to arrive in
#: yuan too — a bid compares a marginal value in yuan against a price, and a
#: missing exchange rate silently rescales the comparison.
LEDGER_CURRENCY: Final = "CNY"

#: US dollar to yuan.  El Salvador has been dollarised since 2001, so its rate
#: sheets are published in USD: tariffs are stored at face value and converted
#: on the way out, which keeps them checkable against the source.  Update this
#: one number when the rate moves; do not edit the scenarios.
USD_CNY: Final = 6.6723


@dataclass(frozen=True, slots=True)
class TariffSpec:
    """A time-of-use electricity tariff, priced in its local currency.

    The three blocks are the ones Salvadoran distribution companies actually
    bill: *punta* (peak), *resto* (shoulder) and *valle* (off-peak).

    The block order here is not a typo.  In El Salvador *valle* costs **more**
    than *resto* — USD 0.207 against 0.178 at medium demand, low voltage,
    August 2026.  Photovoltaic output collapses after sunset while geothermal
    and hydro carry the night, so the cheap hours sit in the middle of the day
    rather than after midnight.  A grower used to Chinese farm tariffs, where
    the valley block is the cheapest thing on the bill and lights run at night,
    has to unlearn that here.
    """

    key: str
    label: str
    valley: float
    flat: float
    peak: float
    currency: str = "USD"
    fx: float = 1.0

    def rate(self, hour: int) -> float:
        """Price for a clock hour in ``[0, 24)``, in the tariff's own currency."""
        return getattr(self, _block_of(hour))

    def rate_ledger(self, hour: int) -> float:
        """The same price converted to the ledger currency (CNY)."""
        return self.rate(hour) * self.fx

    def block(self, hour: int) -> str:
        """Tariff block name for a clock hour."""
        return _block_of(hour)


def _block_of(hour: int) -> str:
    """Map a clock hour to its tariff block.

    The shape here is inherited from the Chinese commercial time-of-use tariff
    this model was first written against: an early-morning and an evening peak
    around a flat middle, with the valley after midnight.  The Salvadoran
    *punta* / *resto* / *valle* hours have not been confirmed against a DGEHM
    rate sheet yet, so treat the clock as a modelling assumption rather than a
    quoted schedule.  It is the only place the shape lives — correcting it is a
    one-function edit, and the three arms share it, so a change cannot
    intro-end a bias into the comparison.
    """
    if 0 <= hour < 6:
        return "valley"
    if 6 <= hour < 10:
        return "flat"
    if 10 <= hour < 12:
        return "peak"
    if 12 <= hour < 17:
        return "flat"
    if 17 <= hour < 21:
        return "peak"
    return "flat"


@dataclass(frozen=True, slots=True)
class SiteProfile:
    """The climate the greenhouse sits in, as hourly forcing parameters.

    ``clear_sky_mj`` is the daily total on a cloudless day; the generator draws
    a daily cloud factor around ``cloud_base`` and shapes the hour with a
    half-sine.  ``cover_transmission`` is the combined PAR transmission of film
    and shade/insect net, the same quantity the physics layer uses.
    """

    key: str
    label: str
    latitude: float
    mean_temp_c: float
    temp_amplitude_k: float
    seasonal_amplitude_k: float
    clear_sky_mj: float
    cloud_base: float
    cloud_spread: float
    cover_transmission: float
    humidity_pct: float


@dataclass(frozen=True, slots=True)
class CropSpec:
    """One bed of one crop: what it wants, what it tolerates, what it is worth.

    ``growth_max_g_m2_day`` and ``dli_half`` parameterise a saturating light
    response ``g(DLI) = g_max · DLI / (DLI + K)`` in grams of *marketable fresh
    mass* per m² per day.  The saturation is what makes the agents' bids
    diminish as their light deficit closes, and therefore what turns a
    winner-take-all auction into a smooth allocation.
    """

    key: str
    label: str
    area_m2: float
    price_yuan_kg: float
    dli_target: float
    light_w_m2: float
    setpoint_day_c: float
    setpoint_night_c: float
    t_opt_c: float
    t_min_c: float
    t_max_c: float
    t_critical_c: float
    damage_per_hour_k: float
    growth_max_g_m2_day: float
    dli_half: float
    fert_g_m2_day: float
    root_zone_mm: float
    kc_mid: float
    depletion_fraction: float
    n_concentration: float
    initial_n_kg_ha: float
    initial_moisture_pct: float
    field_capacity_pct: float
    wilting_point_pct: float
    #: Leaf area index at transplant.  A greenhouse bed starts with a seedling
    #: rather than a seed, so the CropForge backend opens the season with a
    #: partially closed canopy instead of an LAI of zero.
    transplant_lai: float = 0.45

    @property
    def profile_label(self) -> str:
        return CROP_PROFILES[self.key].label

    @property
    def dry_matter_fraction(self) -> float:
        return CROP_PROFILES[self.key].dry_matter_fraction

    def setpoint(self, hour: int) -> float:
        """Daytime setpoint between 06:00 and 18:00, night setpoint otherwise."""
        return self.setpoint_day_c if 6 <= hour < 18 else self.setpoint_night_c

    def temp_factor(self, temp_c: float) -> float:
        """Cardinal temperature response in ``[0, 1]``, triangular between limits."""
        if temp_c <= self.t_min_c or temp_c >= self.t_max_c:
            return 0.0
        if temp_c < self.t_opt_c:
            return (temp_c - self.t_min_c) / (self.t_opt_c - self.t_min_c)
        return (self.t_max_c - temp_c) / (self.t_max_c - self.t_opt_c)

    def damage_rate(self, temp_c: float) -> float:
        """Hourly quality damage above the crop-specific critical temperature."""
        return self.damage_per_hour_k * max(0.0, temp_c - self.t_critical_c)


@dataclass(frozen=True, slots=True)
class GreenhouseSpec:
    """Shared capacities and prices — the constraints that create competition.

    A single grid connection feeds lights, heat pump and pump; the chiller and
    heat pump draw from one thermal capacity; the well delivers a fixed daily
    volume; the fertigation tank holds one day of stock.  Each of these is a
    different market and each has its own clearing rule.
    """

    power_cap_kw: float
    thermal_cap_kw: float
    water_cap_m3_day: float
    fert_cap_kg_day: float
    water_price_yuan_m3: float
    fert_price_yuan_kg: float
    overhead_yuan_m2_day: float
    #: Conversion losses and standing load of the house (fans, controls, pumps).
    standing_kw: float


def _crop(
    key: str,
    *,
    area_m2: float,
    initial_n_kg_ha: float,
    initial_moisture_pct: float,
    field_capacity_pct: float,
    wilting_point_pct: float,
) -> CropSpec:
    """Build a crop spec: its market traits from the template, its bed from here.

    Price, light response and temperature tolerance are properties of the crop,
    so they now live in the template's ``[market]`` table.  ``area_m2`` and the
    soil state are properties of one bed on one site and stay here — three crops
    sharing a house do not share a bed, but they do share a soil profile only by
    coincidence.
    """
    template = TEMPLATES[key]
    market = template.market
    growth = template.growth
    if market is None or growth is None:
        raise ValueError(f"{key} 模板缺少 [market] 或 [growth] 段，经济层无法给它定价")
    water = CROP_WATER[key]
    return CropSpec(
        key=key,
        label=template.label,
        area_m2=area_m2,
        price_yuan_kg=market.price_yuan_kg,
        dli_target=market.dli_target,
        light_w_m2=market.light_w_m2,
        setpoint_day_c=market.setpoint_day_c,
        setpoint_night_c=market.setpoint_night_c,
        t_opt_c=market.t_opt_c,
        t_min_c=market.t_min_c,
        t_max_c=market.t_max_c,
        t_critical_c=market.t_critical_c,
        damage_per_hour_k=market.damage_per_hour_k,
        growth_max_g_m2_day=market.growth_max_g_m2_day,
        dli_half=market.dli_half,
        fert_g_m2_day=market.fert_g_m2_day,
        root_zone_mm=water.root_zone_mm,
        kc_mid=water.kc_mid,
        depletion_fraction=water.depletion_fraction,
        n_concentration=water.n_concentration,
        initial_n_kg_ha=initial_n_kg_ha,
        initial_moisture_pct=initial_moisture_pct,
        field_capacity_pct=field_capacity_pct,
        wilting_point_pct=wilting_point_pct,
        transplant_lai=growth.transplant_lai,
    )


#: Three beds under one roof.  The tomato is the high-value, heat-tolerant
#: crop; the cucumber is a bulk producer; the lettuce is the cheapest to sell
#: and by far the most heat-sensitive, which is what makes it bid aggressively
#: for cooling despite its low price.
CROPS: Final[dict[str, CropSpec]] = {
    "tomato": _crop(
        "tomato",
        area_m2=240.0,
        initial_n_kg_ha=28.0,
        initial_moisture_pct=22.0,
        field_capacity_pct=34.0,
        wilting_point_pct=16.0,
    ),
    "cucumber": _crop(
        "cucumber",
        area_m2=200.0,
        initial_n_kg_ha=25.0,
        initial_moisture_pct=21.0,
        field_capacity_pct=33.0,
        wilting_point_pct=15.0,
    ),
    "lettuce": _crop(
        "lettuce",
        area_m2=160.0,
        initial_n_kg_ha=30.0,
        initial_moisture_pct=20.0,
        field_capacity_pct=32.0,
        wilting_point_pct=14.0,
    ),
}

#: The house itself.  Capacities are sized for a 600 m² three-bay tunnel with a
#: 25 kW service: enough for climate control, not enough for climate control
#: and full lighting at the same time — which is the whole point.
GREENHOUSE: Final = GreenhouseSpec(
    power_cap_kw=36.0,
    thermal_cap_kw=20.0,
    water_cap_m3_day=1.4,
    fert_cap_kg_day=2.0,
    water_price_yuan_m3=4.2,
    fert_price_yuan_kg=5.6,
    overhead_yuan_m2_day=0.0,
    standing_kw=0.8,
)

#: Two sites.  Which resource binds is a property of the site, not of the
#: model: the tropical house fights the chiller, the temperate house fights
#: the heat pump and the light bill.
SITES: Final[dict[str, SiteProfile]] = {
    "tropical": SiteProfile(
        key="tropical",
        label="萨尔瓦多高原 · 雨季",
        latitude=13.69,
        mean_temp_c=23.0,
        temp_amplitude_k=9.5,
        seasonal_amplitude_k=1.4,
        clear_sky_mj=23.0,
        cloud_base=0.58,
        cloud_spread=0.16,
        cover_transmission=0.55,
        humidity_pct=78.0,
    ),
    "temperate": SiteProfile(
        key="temperate",
        label="华北平原 · 冬季",
        latitude=36.85,
        mean_temp_c=-0.5,
        temp_amplitude_k=9.0,
        seasonal_amplitude_k=2.4,
        clear_sky_mj=12.0,
        cloud_base=0.72,
        cloud_spread=0.14,
        cover_transmission=0.65,
        humidity_pct=62.0,
    ),
}

#: Salvadoran retail tariffs, from the AES CLESA end-user rate sheet issued
#: under DGEHM resolutions No. 97/2026/DE and 98/2026/DE, effective 1 August
#: 2026.  Medium demand is 10–50 kW, which is the bracket a 36 kW service
#: falls into; the rates below are the low-voltage (BT) figures.
#:
#: What separates the two scenarios is not how expensive they are but whether
#: the meter *sees time*.  ``sv_horaria`` bills by the hour, so a grower can
#: move load into the cheap block; ``sv_plana`` charges one price all day, so
#: moving load buys nothing.  Putting them side by side is what tells you
#: whether the peak/valley spread is actually large enough to be worth
#: scheduling against — and in this tariff it barely is (1.18×, against 3.9×
#: for the Chinese farm tariff this model originally carried).
#:
#: Note the ordering: *valle* sits above *resto*, not below it.  Solar output
#: is gone by the time the off-peak block starts, so the cheap hours fall in
#: the middle of the day.  Rates are in USD; ``fx`` converts them to the ledger
#: currency — roughly 1.40 / 1.19 / 1.38 元/kWh at the rate above.
TARIFFS: Final[dict[str, TariffSpec]] = {
    "sv_horaria": TariffSpec(
        key="sv_horaria",
        label="萨尔瓦多 · 中需求 BT 分时计量",
        peak=0.209986,
        flat=0.178033,
        valley=0.206997,
        currency="USD",
        fx=USD_CNY,
    ),
    "sv_plana": TariffSpec(
        key="sv_plana",
        label="萨尔瓦多 · 中需求 BT 统一费率",
        peak=0.186401,
        flat=0.186401,
        valley=0.186401,
        currency="USD",
        fx=USD_CNY,
    ),
}
