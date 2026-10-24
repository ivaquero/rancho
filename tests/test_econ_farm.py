"""Tests for the backend that runs an actual CropForge season.

The coupled backend is the only one that can be wrong in a way the market
cannot see: a season that quietly produces nothing, a response table measured
against the wrong day, a canopy that never starts.  These tests pin those down
with short seasons, because the properties are structural and three days are
enough to show them.

A Farm season costs ~10 ms; the response tables are still built once per
module and copied before any test advances their day cursor.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from rancho.econ.farm import (
    CONVERGENCE,
    FarmSeason,
    FarmTable,
    Season,
    _unlogged,
    measure_table,
    run_season,
    season_run,
)
from rancho.econ.spec import CROPS, MOL_PAR_PER_MJ

#: Three short days of a plausible tropical greenhouse: 12 mol of PAR on day
#: one, heavy overcast on day two, bright on day three.
_DAYS = 3
_SEASON = Season(
    radiation_mj=(12.0 / MOL_PAR_PER_MJ, 5.0 / 3.0, 18.0 / 3.0),
    temp_mean=(23.0, 20.5, 25.0),
    temp_max=(28.5, 24.0, 31.0),
)


@pytest.fixture(scope="module")
def tomato_table() -> FarmTable:
    return measure_table(CROPS["tomato"], _SEASON)


@pytest.fixture(scope="module")
def lettuce_table() -> FarmTable:
    return measure_table(CROPS["lettuce"], _SEASON)


def test_a_farm_season_returns_one_non_negative_increment_per_day() -> None:
    growth = run_season(CROPS["tomato"], _SEASON)
    assert len(growth) == _DAYS
    assert all(value >= 0.0 for value in growth)


def test_skipping_the_state_log_costs_time_but_not_physics() -> None:
    """``_unlogged`` is a shortcut around CropForge's Parquet log, not a knob.

    It must change the timing and nothing else, so the same season run with and
    without it has to agree exactly -- not approximately.
    """
    logged = run_season(CROPS["tomato"], _SEASON)
    with _unlogged():
        quiet = run_season(CROPS["tomato"], _SEASON)
    assert quiet == logged


def test_the_season_starts_from_a_transplanted_seedling() -> None:
    """Day one must already intercept light, which needs a canopy to exist.

    CropForge builds its plants at germination (leaf area 0.03).  Without the
    seedling start the first days would produce almost nothing — the exact bug
    this backend shipped with, and the reason the assertion is on day one.
    """
    result = season_run(CROPS["tomato"], _SEASON)
    assert result.dry_matter[0] > 0.0
    assert result.final_lai >= CROPS["tomato"].transplant_lai


def test_more_light_produces_more_dry_matter() -> None:
    base = sum(run_season(CROPS["cucumber"], _SEASON))
    bright = sum(run_season(CROPS["cucumber"], _SEASON, light_scale=1.2))
    assert bright > base


def test_the_measured_table_matches_the_baseline_run_it_came_from(
    lettuce_table: FarmTable,
) -> None:
    table = replace(lettuce_table, day=0)
    baseline = season_run(CROPS["lettuce"], _SEASON).dry_matter
    fresh = 1.0 / CROPS["lettuce"].dry_matter_fraction
    for day, dry in enumerate(baseline):
        # The anchor is the baseline itself, so evaluating at the reference
        # light must return it — not a nearby number.  The cursor has to move
        # with the loop: the table is indexed by day, not by the argument.
        assert table.production(table.reference[day]) == pytest.approx(dry * fresh, rel=1e-9)
        table.settle(table.reference[day], 22.0)


def test_the_table_prices_a_photon_above_zero_and_is_flat_within_the_day(
    tomato_table: FarmTable,
) -> None:
    assert all(slope > 0.0 for slope in tomato_table.slope)
    first = tomato_table.marginal(0.0)
    assert tomato_table.marginal(50.0) == first


def test_the_table_never_prices_negative_production(tomato_table: FarmTable) -> None:
    table = replace(tomato_table, day=0)
    # Far below the anchor a negative intercept would show up as a negative bid
    # rather than a clamp.
    assert table.production(0.0) >= 0.0
    assert table.production(-1e6) == 0.0


def test_the_table_advances_one_day_per_settle_and_stops_at_the_last(
    lettuce_table: FarmTable,
) -> None:
    table = replace(lettuce_table, day=0)
    assert table.day == 0
    for _ in range(_DAYS + 5):
        table.settle(10.0, 22.0)
    assert table.day == _DAYS + 5
    # Past the end it holds the last day rather than indexing off the list.
    assert table.marginal(10.0) == max(0.0, table.slope[-1])


def test_an_empty_day_has_no_marginal_value() -> None:
    dark = Season(radiation_mj=(0.0, 0.0), temp_mean=(22.0, 22.0), temp_max=(26.0, 26.0))
    table = measure_table(CROPS["lettuce"], dark)
    assert table.slope == (0.0, 0.0)
    assert table.base == (0.0, 0.0)


def test_the_season_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError):
        Season(radiation_mj=(1.0, 2.0), temp_mean=(20.0,), temp_max=(25.0, 25.0))


def test_the_coupled_probe_keeps_the_null_check_bit_exact() -> None:
    """The allocator self-check must survive a stateful production function.

    Both arms are handed the same response table; if the table carried a day
    cursor that leaked between arms, ``null`` would start mid-season and the
    gap would show up here.
    """
    from rancho.econ.probe import probe

    result = probe(
        days=2,
        seed=42,
        growth="farm",
        passes=2,
        tariff_keys=("sv_horaria",),
        crop_keys=("tomato", "lettuce"),
    )
    verdict = result.regimes[0].verdict
    assert verdict.null_gap == 0.0
    assert result.coupling is not None
    assert result.coupling["passes"] >= 1
    assert result.coupling["grid"] == FarmSeason().grid


def test_the_coupled_probe_reports_a_converged_loop() -> None:
    from rancho.econ.probe import probe

    result = probe(
        days=2,
        seed=42,
        growth="farm",
        passes=3,
        tariff_keys=("sv_horaria",),
        crop_keys=("tomato",),
    )
    coupling = result.coupling
    assert coupling is not None
    assert coupling["residual"] is not None
    # Either it settled below the tolerance, or it used every pass it was given.
    assert coupling["converged"] is (coupling["residual"] < CONVERGENCE) or coupling["passes"] == 3


def test_the_coupled_backend_is_deterministic() -> None:
    from rancho.econ.probe import probe

    def run(seed: int) -> dict[str, object]:
        result = probe(
            days=1,
            seed=seed,
            growth="farm",
            passes=2,
            tariff_keys=("sv_horaria",),
            crop_keys=("tomato",),
        )
        payload = result.to_dict()
        payload.pop("generated_at")
        return payload

    assert run(42) == run(42)
    assert run(42)["regimes"] != run(7)["regimes"]


def test_the_farm_and_the_plugin_backends_agree_on_the_same_season() -> None:
    """A sanity check on the whole exercise: same equations, same answer.

    The Farm adds a grid, the engine's step ordering and a transplanted start —
    not different physics.  If the two backends disagreed by more than a
    couple of percent, one of them would be applying the season twice.
    """
    from rancho.econ.growth import build_growth

    plugin = build_growth("cropforge", CROPS["tomato"])
    plugin_total = 0.0
    for day in range(_DAYS):
        dli = _SEASON.radiation_mj[day] * MOL_PAR_PER_MJ
        # The plugin is stepped potential here, exactly as the market does it.
        plugin_total += plugin.production(dli)
        plugin.settle(dli, _SEASON.temp_mean[day])

    # Fresh mass on both sides: the Farm reports dry matter, the plugin reports
    # marketable fresh mass, and comparing them raw is a factor of ~17 apart.
    fresh = 1.0 / CROPS["tomato"].dry_matter_fraction
    farm_total = sum(run_season(CROPS["tomato"], _SEASON)) * fresh
    assert farm_total == pytest.approx(plugin_total, rel=0.02)


def test_the_table_is_usable_as_a_light_response(tomato_table: FarmTable) -> None:
    assert isinstance(tomato_table, FarmTable)
    assert tomato_table.name == "farm"
    assert isinstance(tomato_table.production(15.0), float)
    assert isinstance(tomato_table.marginal(15.0), float)
