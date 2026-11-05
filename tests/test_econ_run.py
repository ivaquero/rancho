"""End-to-end invariants of one arm and of the full probe.

These run the real model, so the windows are short — a day or two is enough for
the market to bind and for the accounting to be checked against the physics.
"""

from __future__ import annotations

import json

import pytest
from support import one_day_forcing

from rancho.econ.model import ArmResult, GreenhouseModel, run_arm
from rancho.econ.probe import NULL_TOLERANCE, probe
from rancho.econ.spec import (
    CROPS,
    GREENHOUSE,
    HOURS_PER_DAY,
    MOL_PAR_PER_MJ,
    SITES,
    TARIFFS,
)

CROPS_3 = (CROPS["tomato"], CROPS["cucumber"], CROPS["lettuce"])
TOLERANCE = 1e-6


def _run(
    days: int,
    seed: int = 42,
    arm: str = "pooled",
    cap_scale: float = 1.0,
    growth: str = "analytic",
) -> ArmResult:
    return run_arm(
        site=SITES["tropical"],
        tariff=TARIFFS["sv_horaria"],
        crops=CROPS_3,
        days=days,
        seed=seed,
        arm=arm,
        cap_scale=cap_scale,
        growth=growth,
    )


@pytest.fixture(scope="module")
def pooled() -> ArmResult:
    return _run(days=2)


def test_one_arm_logs_an_hour_per_step_and_a_record_per_crop_day(pooled: ArmResult) -> None:
    assert len(pooled.hours) == 2 * 24
    assert len(pooled.records) == 2 * len(CROPS_3)
    assert sorted({record.day for record in pooled.records}) == [1, 2]
    assert [hour.hour for hour in pooled.hours] == list(range(2 * 24))


def test_no_hour_draws_more_than_the_connection_or_the_thermal_capacity(pooled: ArmResult) -> None:
    for hour in pooled.hours:
        assert hour.served_hvac_kw <= GREENHOUSE.thermal_cap_kw + TOLERANCE
        # Lighting competes for what climate control and standing load leave.
        assert hour.served_light_kw + GREENHOUSE.standing_kw <= GREENHOUSE.power_cap_kw + TOLERANCE


def test_delivery_never_exceeds_the_demand_it_cleared_against(pooled: ArmResult) -> None:
    for hour in pooled.hours:
        assert hour.served_hvac_kw <= hour.demand_hvac_kw + TOLERANCE
        assert hour.served_light_kw <= hour.demand_light_kw + TOLERANCE


def test_the_daylight_still_ahead_is_the_tail_of_the_days_integral() -> None:
    """The carried remainder must be today's DLI minus the hours already past.

    Easy to get off by one hour, and the error would only show up as a slightly
    wrong light bid, never as a broken invariant anywhere else.
    """
    forcing = one_day_forcing()
    model = GreenhouseModel(forcing=forcing, crops=CROPS_3, seed=1)
    daily = forcing.dli(0)

    noon = sum(forcing.radiation_in[hour] * MOL_PAR_PER_MJ for hour in range(12))
    model.run_for(12)
    assert model.greenhouse.dli_left == pytest.approx(daily - noon, abs=1e-6)

    model.run_for(HOURS_PER_DAY - 12)
    assert model.greenhouse.dli_left == pytest.approx(0.0, abs=1e-6)


def test_a_scarcity_price_only_appears_when_something_was_cut(pooled: ArmResult) -> None:
    for hour in pooled.hours:
        cut = hour.demand_hvac_kw - hour.served_hvac_kw
        if hour.shadow_thermal > 0.0:
            assert cut > TOLERANCE


def test_cumulative_cost_is_monotone_and_matches_its_daily_increment(pooled: ArmResult) -> None:
    """Catches the class of bug where a daily column is fed the running total."""
    for crop in CROPS_3:
        rows = [record for record in pooled.records if record.crop == crop.key]
        series = [record.cost_cum for record in rows]
        assert series == sorted(series)
        previous = 0.0
        for record in rows:
            daily = (
                record.cost_light
                + record.cost_hvac
                + record.cost_water
                + record.cost_fert
                + record.cost_overhead
            )
            assert record.cost_cum - previous == pytest.approx(daily, abs=5e-3)
            previous = record.cost_cum


def test_unit_cost_is_the_ratio_of_the_two_cumulative_columns(pooled: ArmResult) -> None:
    """A reader of ``daily.csv`` must be able to recompute the unit cost.

    Both columns are rounded to three decimals for the file, so the identity
    only holds to display precision — but not to within a factor of the day
    count, which is what a daily (rather than cumulative) denominator gives.
    """
    for record in pooled.records:
        if record.yield_cum_kg > 1e-9:
            expected = record.cost_cum / record.yield_cum_kg
            assert record.unit_cost == pytest.approx(expected, rel=1e-3)


def test_a_generous_capacity_reproduces_the_unlimited_arm() -> None:
    """The null arm: same arithmetic, different capacity, same answer."""
    solo = _run(days=1, arm="solo", cap_scale=1.0e4)
    null = _run(days=1, arm="null", cap_scale=1.0e3)
    for crop in solo.crops:
        counterpart = null.by_crop()[crop.key]
        assert counterpart.yield_kg == pytest.approx(crop.yield_kg, abs=NULL_TOLERANCE)
        assert counterpart.cost_total == pytest.approx(crop.cost_total, abs=NULL_TOLERANCE)


def test_the_same_seed_reproduces_the_run() -> None:
    first, second = _run(days=1), _run(days=1)
    assert [crop.as_dict() for crop in first.crops] == [crop.as_dict() for crop in second.crops]


def test_the_cropforge_backend_carries_a_canopy_and_the_analytic_one_does_not() -> None:
    cropforge = _run(days=2, growth="cropforge")
    assert all(crop.lai > 0.0 for crop in cropforge.crops)
    assert all(crop.lai == 0.0 for crop in _run(days=1).crops)


def test_the_null_check_also_holds_for_the_cropforge_backend() -> None:
    solo = _run(days=1, arm="solo", cap_scale=1.0e4, growth="cropforge")
    null = _run(days=1, arm="null", cap_scale=1.0e3, growth="cropforge")
    for crop in solo.crops:
        counterpart = null.by_crop()[crop.key]
        assert counterpart.yield_kg == pytest.approx(crop.yield_kg, abs=NULL_TOLERANCE)


def test_a_small_probe_serialises_and_keeps_its_null_check_green() -> None:
    result = probe(days=2, seed=42, tariff_keys=("sv_horaria",), crop_keys=("tomato", "cucumber"))
    regime = result.regimes[0]
    assert regime.verdict.null_ok is True
    assert regime.verdict.null_gap <= NULL_TOLERANCE
    assert len(result.id) == 12

    payload = json.loads(json.dumps(result.to_dict()))
    assert payload["days"] == 2
    # The probe defaults to the plugin backend since phase 2; the analytic
    # proxy is opt-in.
    assert payload["growth"] == "cropforge"
    assert payload["coupling"] is None
    assert set(payload["regimes"][0]) == {
        "tariff",
        "forcing",
        "arms",
        "comparisons",
        "verdict",
        "series",
    }
    assert len(payload["regimes"][0]["series"]["tomato"]["pooled_cost"]) == 2


def test_the_coldest_night_hours_price_the_whole_days_crop() -> None:
    """At 00:00 accumulated DLI is zero; valuation must not depend on it.

    At the bundled tariffs heating a −5 °C night is genuinely uneconomic, so
    the crops are priced up to isolate *valuation* from the bid threshold.
    Under the old "DLI that has arrived so far" rule the first hours valued
    the crop at zero and would never quote at any price.
    """
    from dataclasses import replace

    rich = tuple(replace(spec, price_yuan_kg=spec.price_yuan_kg * 20.0) for spec in CROPS_3)
    arm = run_arm(
        site=SITES["temperate"],
        tariff=TARIFFS["sv_horaria"],
        crops=rich,
        days=1,
        seed=42,
        arm="pooled",
    )
    night = [hour for hour in arm.hours if hour.hour_of_day < 7]
    assert night
    assert sum(hour.demand_hvac_kw for hour in night) > 0.0


def test_a_tiny_grid_connection_is_never_overdrawn() -> None:
    """Clearing at a fixed COP 3.2 let a 1 kW connection feed ~1.18 kW.

    The pump is checked at STOCK_HOUR too: pumping energy is billed, so it
    must also be reserved against the same connection rather than just added
    to the invoice.
    """
    from dataclasses import replace

    from rancho.econ.agents import STOCK_HOUR
    from rancho.econ.forcing import build_forcing
    from rancho.econ.spec import PUMP_KWH_PER_M3

    tiny = replace(GREENHOUSE, power_cap_kw=1.0)
    forcing = build_forcing(
        SITES["temperate"],
        TARIFFS["sv_horaria"],
        days=1,
        seed=42,
        water_price_base=tiny.water_price_yuan_m3,
        fert_price_base=tiny.fert_price_yuan_kg,
    )
    model = GreenhouseModel(forcing=forcing, crops=CROPS_3, greenhouse=tiny, seed=42)
    for _ in range(HOURS_PER_DAY):
        # Mesa types ``Model.step`` through a descriptor that ty reads as a
        # free function; calling it on the class keeps the checker happy and
        # runs exactly the same override ``run_for`` would call.
        GreenhouseModel.step(model)
        hour_of_day = (model.hour - 1) % HOURS_PER_DAY
        drawn = tiny.standing_kw + sum(
            agent.hvac_kw_electric + agent.light_kw for agent in model.crop_agents
        )
        if hour_of_day == STOCK_HOUR:
            drawn += sum(agent.m3_water_day for agent in model.crop_agents) * PUMP_KWH_PER_M3
        assert drawn <= tiny.power_cap_kw + TOLERANCE


def test_the_economy_prices_the_field_engines_fao56_water_stress() -> None:
    """Readily available water is (1-p)·(FC-WP), not p·(FC-WP)."""
    from rancho.agronomy import water_stress_ks

    model = GreenhouseModel(
        forcing=one_day_forcing(), crops=CROPS_3, greenhouse=GREENHOUSE, seed=42
    )
    for agent in model.crop_agents:
        spec = agent.spec
        p = spec.depletion_fraction
        fc, wp = spec.field_capacity_pct, spec.wilting_point_pct
        expected = min(1.0, max(0.0, (agent.moisture_pct - wp) / ((1.0 - p) * (fc - wp))))
        assert agent.water_factor() == pytest.approx(expected)
        assert agent.water_factor() == pytest.approx(
            float(water_stress_ks(agent.moisture_pct, wp, fc, p))
        )


def test_marketable_fresh_yield_applies_harvest_index_and_grade() -> None:
    """Both growth backends must sell the same crop the field engine does."""
    from rancho.scenario import CROP_PROFILES

    for key, spec in CROPS.items():
        profile = CROP_PROFILES[key]
        assert spec.fresh_per_dry == pytest.approx(
            profile.harvest_index * profile.marketable_fraction / profile.dry_matter_fraction
        )
        assert spec.fresh_per_dry < 1.0 / profile.dry_matter_fraction
