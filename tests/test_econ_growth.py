"""Tests for the two production functions the market can price against.

The analytic backend is a closed form, so it is checked against its own
definition and against a numerical derivative.  The CropForge backend is
stateful, so it is checked for the properties the bids rely on: monotonicity in
light, a derivative that matches the production it prices, and a canopy that
actually grows.
"""

from __future__ import annotations

import pytest

from rancho.econ.growth import (
    AnalyticGrowth,
    CropForgeGrowth,
    build_growth,
)
from rancho.econ.spec import CROPS


def _numeric_marginal(growth, dli: float, step: float = 1e-4) -> float:
    return (growth.production(dli + step) - growth.production(dli - step)) / (2 * step)


@pytest.mark.parametrize("dli", [0.5, 4.0, 11.0, 25.0, 60.0])
def test_analytic_marginal_is_the_derivative_of_its_own_production(dli: float) -> None:
    growth = AnalyticGrowth(CROPS["tomato"])
    assert growth.marginal(dli) == pytest.approx(_numeric_marginal(growth, dli), rel=1e-6)


def test_analytic_production_is_the_documented_saturating_response() -> None:
    spec = CROPS["tomato"]
    growth = AnalyticGrowth(spec)
    dli = 13.3
    assert growth.production(dli) == pytest.approx(
        spec.growth_max_g_m2_day * dli / (dli + spec.dli_half)
    )
    assert growth.production(0.0) == 0.0


def test_analytic_production_saturates_while_light_keeps_growing() -> None:
    growth = AnalyticGrowth(CROPS["tomato"])
    low, mid, high = growth.production(5.0), growth.production(20.0), growth.production(60.0)
    assert low < mid < high
    # Doubling from 20 to 40 buys less than the first doubling did.
    assert growth.production(40.0) - mid < mid - low


def test_an_unknown_backend_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown growth backend"):
        build_growth("photosynthesis-magic", CROPS["tomato"])


@pytest.mark.parametrize("key", ["tomato", "cucumber", "lettuce"])
def test_the_cropforge_backend_starts_at_transplant(key: str) -> None:
    spec = CROPS[key]
    growth = CropForgeGrowth.build(spec)
    assert growth.plant.phenological_stage == "vegetative"
    assert growth.lai == pytest.approx(spec.transplant_lai)
    assert 0.0 < growth.canopy_fraction() < 1.0


@pytest.mark.parametrize("key", ["tomato", "cucumber", "lettuce"])
def test_cropforge_production_is_linear_in_light_at_a_fixed_canopy(key: str) -> None:
    """Beer-Lambert is linear in incident radiation while the LAI is fixed.

    This is the substantive difference from the analytic backend: the bid does
    not fall as a crop's deficit closes, it stays at one price per photon until
    the canopy changes.
    """
    growth = CropForgeGrowth.build(CROPS[key])
    assert growth.production(20.0) == pytest.approx(2.0 * growth.production(10.0))
    assert growth.production(0.0) == 0.0


@pytest.mark.parametrize("key", ["tomato", "cucumber", "lettuce"])
def test_cropforge_marginal_matches_the_production_it_prices(key: str) -> None:
    growth = CropForgeGrowth.build(CROPS[key])
    for dli in (4.0, 11.0, 30.0):
        assert growth.marginal(dli) == pytest.approx(_numeric_marginal(growth, dli), rel=1e-5)


@pytest.mark.parametrize("key", ["tomato", "cucumber", "lettuce"])
def test_the_canopy_grows_when_the_day_is_settled(key: str) -> None:
    growth = CropForgeGrowth.build(CROPS[key])
    start = growth.lai
    for _ in range(10):
        growth.settle(13.0, 23.0)
    assert growth.lai > start
    assert growth.production(13.0) > 0.0


def test_a_colder_day_builds_less_canopy_than_a_warm_one() -> None:
    """Thermal time, not light, is what closes the canopy."""
    warm = CropForgeGrowth.build(CROPS["tomato"])
    cold = CropForgeGrowth.build(CROPS["tomato"])
    for _ in range(10):
        warm.settle(13.0, 26.0)
        cold.settle(13.0, 14.0)
    assert warm.lai > cold.lai


def test_a_dark_day_builds_no_canopy_and_yields_nothing() -> None:
    growth = CropForgeGrowth.build(CROPS["tomato"])
    assert growth.production(0.0) == 0.0
    growth.settle(0.0, 23.0)
    assert growth.production(0.0) == 0.0
