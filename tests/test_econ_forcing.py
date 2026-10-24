"""Tests for the exogenous series the market runs on.

Only the properties the rest of the model relies on: the tariff shape, the
daylight window, reproducibility, and a bounded water price.  The last one is a
regression — a unit-variance AR(1) innovation once let the irrigation price
drift 61 % below base and off into negative territory.
"""

from __future__ import annotations

import pytest

from rancho.econ.forcing import SUNRISE_HOUR, SUNSET_HOUR, build_forcing
from rancho.econ.spec import GREENHOUSE, HOURS_PER_DAY, SITES, TARIFFS

WATER_BASE = GREENHOUSE.water_price_yuan_m3


def _forcing(days: int = 28, seed: int = 42):
    return build_forcing(
        SITES["tropical"],
        TARIFFS["sv_horaria"],
        days=days,
        seed=seed,
        water_price_base=GREENHOUSE.water_price_yuan_m3,
        fert_price_base=GREENHOUSE.fert_price_yuan_kg,
    )


def test_the_tariff_keeps_its_documented_block_shape() -> None:
    tariff = TARIFFS["sv_horaria"]
    blocks = [tariff.block(hour) for hour in range(HOURS_PER_DAY)]
    assert blocks == (
        ["valley"] * 6 + ["flat"] * 4 + ["peak"] * 2 + ["flat"] * 5 + ["peak"] * 4 + ["flat"] * 3
    )
    assert tariff.rate(3) == tariff.valley
    assert tariff.rate(11) == tariff.peak
    assert tariff.rate(15) == tariff.flat


def test_radiation_is_zero_outside_the_daylight_window() -> None:
    forcing = _forcing(days=3)
    for hour, radiation in enumerate(forcing.radiation_in):
        if hour % HOURS_PER_DAY < SUNRISE_HOUR or hour % HOURS_PER_DAY >= SUNSET_HOUR:
            # The half-sine is evaluated at the horizon on the sunset hour,
            # which leaves a 1e-16 rounding residue rather than a clean zero.
            assert radiation == pytest.approx(0.0, abs=1e-12)


def test_water_price_stays_inside_the_ten_percent_band() -> None:
    forcing = _forcing(days=28)
    assert min(forcing.water_price) >= WATER_BASE * 0.9 - 1e-9
    assert max(forcing.water_price) <= WATER_BASE * 1.1 + 1e-9
    assert all(price > 0.0 for price in forcing.water_price)


def test_the_water_price_moves_day_to_day_rather_than_hour_to_hour() -> None:
    forcing = _forcing(days=5)
    for day in range(5):
        hours = forcing.day_hours(day)
        assert len({forcing.water_price[hour] for hour in hours}) == 1


def test_the_same_seed_rebuilds_the_same_series() -> None:
    assert _forcing(days=7, seed=3) == _forcing(days=7, seed=3)


def test_a_different_seed_moves_the_weather() -> None:
    assert _forcing(days=7, seed=3).temp_out != _forcing(days=7, seed=4).temp_out


@pytest.mark.parametrize("days", [0, -1])
def test_a_non_positive_horizon_is_rejected(days: int) -> None:
    with pytest.raises(ValueError, match="days must be positive"):
        _forcing(days=days)
