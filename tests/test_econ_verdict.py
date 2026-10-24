"""Unit tests for the three-arm verdict.

The verdict turns three runs into a claim, so every threshold gets a case that
sits just on the failing side of it: a null arm that drifts, a house with slack
capacity, a shortfall that is shared proportionally, and finally a shortfall
that lands on one bed.
"""

from __future__ import annotations

import pytest
from support import make_arm, make_snapshot

from rancho.econ.probe import (
    MIN_PRESSURE,
    MIN_UNIT_SHIFT,
    NULL_TOLERANCE,
    _compare,
    _ranks,
    _relative,
    _spearman,
    _verdict,
)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([10.0, 20.0, 30.0], [1.0, 2.0, 3.0]),
        ([1.0, 1.0, 3.0], [1.5, 1.5, 3.0]),
        ([3.0, 3.0, 1.0], [2.5, 2.5, 1.0]),
        ([5.0, 5.0, 5.0], [2.0, 2.0, 2.0]),
        ([5.0], [1.0]),
    ],
)
def test_tied_values_share_their_average_rank(values: list[float], expected: list[float]) -> None:
    assert _ranks(values) == expected


def test_spearman_is_one_for_a_monotone_increase() -> None:
    assert _spearman([1.0, 2.0, 3.0], [10.0, 20.0, 35.0]) == pytest.approx(1.0)


def test_spearman_is_minus_one_for_a_monotone_decrease() -> None:
    assert _spearman([1.0, 2.0, 3.0], [35.0, 20.0, 10.0]) == pytest.approx(-1.0)


def test_spearman_holds_up_when_both_sides_have_ties() -> None:
    assert _spearman([1.0, 2.0, 2.0, 3.0], [5.0, 6.0, 6.0, 9.0]) == pytest.approx(1.0)


def test_a_constant_series_has_no_rank_correlation_and_reports_zero() -> None:
    assert _spearman([1.0, 2.0, 3.0], [4.0, 4.0, 4.0]) == 0.0
    assert _spearman([1.0], [2.0]) == 0.0


def test_relative_change_guards_against_a_zero_base() -> None:
    assert _relative(110.0, 100.0) == pytest.approx(0.1)
    assert _relative(5.0, 0.0) == 0.0


def test_compare_prices_pressure_against_what_the_crop_asked_for() -> None:
    solo = make_arm("solo", [make_snapshot("tomato", yield_kg=100.0, cost_total=300.0)])
    pooled = make_arm(
        "pooled",
        [
            make_snapshot(
                "tomato",
                yield_kg=90.0,
                cost_total=240.0,
                thermal_kwh=40.0,
                light_kwh=10.0,
                denied_hvac=30.0,
                denied_light=20.0,
            )
        ],
    )
    (item,) = _compare(solo, pooled)
    # Requested = 40 + 10 + 30 + 20 = 100; denied 50 of it.
    assert item.pressure == pytest.approx(0.5)
    assert item.denied_share == pytest.approx(1.0)
    assert item.delta_unit == pytest.approx((240.0 / 90.0 - 3.0) / 3.0)
    assert item.delta_yield == pytest.approx(-0.1)


def test_denied_share_is_the_crops_slice_of_the_house_total() -> None:
    solo = make_arm(
        "solo",
        [
            make_snapshot("a", yield_kg=100.0, cost_total=300.0),
            make_snapshot("b", yield_kg=100.0, cost_total=300.0),
        ],
    )
    pooled = make_arm(
        "pooled",
        [
            make_snapshot(
                "a", yield_kg=100.0, cost_total=300.0, thermal_kwh=100.0, denied_hvac=30.0
            ),
            make_snapshot(
                "b", yield_kg=100.0, cost_total=300.0, thermal_kwh=100.0, denied_hvac=70.0
            ),
        ],
    )
    first, second = _compare(solo, pooled)
    assert first.denied_share == pytest.approx(0.3)
    assert second.denied_share == pytest.approx(0.7)
    assert first.pressure == pytest.approx(30.0 / 130.0)


def test_a_null_arm_that_drifts_from_solo_fails_before_anything_else() -> None:
    solo = make_arm("solo", [make_snapshot("tomato", yield_kg=100.0, cost_total=300.0)])
    pooled = make_arm(
        "pooled",
        [
            make_snapshot(
                "tomato", yield_kg=90.0, cost_total=255.0, thermal_kwh=20.0, denied_hvac=20.0
            )
        ],
    )
    null = make_arm("null", [make_snapshot("tomato", yield_kg=94.0, cost_total=282.0)])
    verdict = _verdict(solo, pooled, null, _compare(solo, pooled))
    assert verdict.null_gap == pytest.approx(0.06)
    assert verdict.null_ok is False
    assert verdict.passed is False
    assert "分配器自检" in verdict.summary


def test_slack_capacity_is_reported_as_no_competition() -> None:
    crops = [make_snapshot(key, yield_kg=100.0, cost_total=300.0, thermal_kwh=50.0) for key in "ab"]
    solo = make_arm("solo", crops)
    pooled = make_arm("pooled", crops)
    verdict = _verdict(solo, pooled, make_arm("null", crops), _compare(solo, pooled))
    assert verdict.pressure == 0.0
    assert verdict.null_ok is True
    assert verdict.passed is False
    assert "容量基本不紧" in verdict.summary


def test_a_proportional_haircut_is_not_differentiation() -> None:
    keys = ("a", "b", "c")
    solo = make_arm("solo", [make_snapshot(key, yield_kg=100.0, cost_total=300.0) for key in keys])
    pooled = make_arm(
        "pooled",
        [
            make_snapshot(
                key, yield_kg=90.0, cost_total=240.0, thermal_kwh=100.0, denied_hvac=50.0 * factor
            )
            for key, factor in zip(keys, (1.0, 2.0, 3.0), strict=True)
        ],
    )
    verdict = _verdict(solo, pooled, make_arm("null", solo.crops), _compare(solo, pooled))
    assert verdict.spread == pytest.approx(0.0, abs=1e-12)
    assert verdict.differentiated is False
    assert verdict.passed is False
    assert "按比例分摊" in verdict.summary


def test_a_shortfall_below_the_pressure_floor_is_not_a_competition_result() -> None:
    """A few percent of denial is noise, not scarcity.

    Built to pass the differentiation test and fail only the pressure floor, so
    the floor is what the assertion is about.
    """
    solo = make_arm(
        "solo",
        [
            make_snapshot("a", yield_kg=100.0, cost_total=300.0),
            make_snapshot("b", yield_kg=100.0, cost_total=300.0),
        ],
    )
    pooled = make_arm(
        "pooled",
        [
            make_snapshot(
                "a", yield_kg=85.0, cost_total=210.0, thermal_kwh=990.0, denied_hvac=30.0
            ),
            make_snapshot("b", yield_kg=100.0, cost_total=300.0, thermal_kwh=990.0),
        ],
    )
    verdict = _verdict(solo, pooled, make_arm("null", solo.crops), _compare(solo, pooled))
    assert verdict.pressure == pytest.approx(30.0 / 1020.0)
    assert verdict.pressure < MIN_PRESSURE
    assert verdict.differentiated is True
    assert verdict.passed is False


def test_differentiation_needs_more_than_a_two_percent_shift_on_some_crop() -> None:
    """Spreading 3 % across the house is not the same as one bed moving 3 %."""
    solo = make_arm(
        "solo",
        [make_snapshot(key, yield_kg=100.0, cost_total=300.0) for key in ("a", "b", "c")],
    )
    pooled = make_arm(
        "pooled",
        [
            make_snapshot(
                key,
                yield_kg=100.0,
                cost_total=3.0 * factor * 100.0,
                thermal_kwh=100.0,
                denied_hvac=60.0,
            )
            for key, factor in zip(("a", "b", "c"), (1.015, 1.0, 0.985), strict=True)
        ],
    )
    comparisons = _compare(solo, pooled)
    verdict = _verdict(solo, pooled, make_arm("null", solo.crops), comparisons)
    assert verdict.spread == pytest.approx(0.03)
    assert max(abs(item.delta_unit) for item in comparisons) < MIN_UNIT_SHIFT
    assert verdict.differentiated is False


def test_a_concentrated_shortfall_passes_and_names_the_binding_crop() -> None:
    keys = ("tomato", "cucumber", "lettuce")
    solo = make_arm("solo", [make_snapshot(key, yield_kg=100.0, cost_total=300.0) for key in keys])
    pooled = make_arm(
        "pooled",
        [
            make_snapshot(
                "tomato", yield_kg=99.0, cost_total=295.0, thermal_kwh=100.0, denied_hvac=5.0
            ),
            make_snapshot(
                "cucumber", yield_kg=80.0, cost_total=200.0, thermal_kwh=100.0, denied_hvac=85.0
            ),
            make_snapshot(
                "lettuce", yield_kg=98.0, cost_total=298.0, thermal_kwh=100.0, denied_hvac=10.0
            ),
        ],
    )
    comparisons = _compare(solo, pooled)
    verdict = _verdict(solo, pooled, make_arm("null", solo.crops), comparisons)

    assert verdict.null_ok is True
    assert verdict.null_gap <= NULL_TOLERANCE
    assert verdict.passed is True
    assert verdict.binding_crop == "Cucumber"
    assert verdict.pressure == pytest.approx(85.0 / 185.0)
    assert verdict.pressure > MIN_PRESSURE
    # Averaging hides the starved bed: 25 % house-wide against 46 % on the bed
    # that lost the auction.
    assert verdict.average_pressure == pytest.approx(100.0 / 400.0)
    assert verdict.average_pressure < verdict.pressure
    # The bed that lost the most also cut its unit cost the most.
    assert verdict.rho == pytest.approx(-0.5)
    assert "通过" in verdict.summary
    assert "Cucumber" in verdict.summary
