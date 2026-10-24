"""Unit tests for the merit-order allocator and its denial accounting.

The allocator is the only place where one crop's gain becomes another crop's
loss, so its invariants — conservation, capacity, determinism, and a per-crop
denial that follows the whole multi-step bid curve — are what the probe's
verdict rests on.
"""

from __future__ import annotations

import pytest

from rancho.econ.agents import Quote, clear, denied_by_crop


def test_slack_capacity_serves_every_bid_and_prices_capacity_at_zero() -> None:
    quotes = [Quote("a", 10.0, 5.0), Quote("b", 5.0, 3.0)]
    served, shadow = clear(quotes, 100.0)
    assert served == {"a": 10.0, "b": 5.0}
    assert shadow == 0.0


def test_merit_order_fills_the_top_bid_first_and_stops_inside_the_marginal_one() -> None:
    quotes = [Quote("low", 10.0, 2.0), Quote("high", 10.0, 6.0), Quote("mid", 10.0, 4.0)]
    served, shadow = clear(quotes, 15.0)
    assert served["high"] == 10.0
    assert served["mid"] == 5.0
    assert served["low"] == 0.0
    assert shadow == 4.0


@pytest.mark.parametrize("capacity", [0.0, 3.0, 12.0, 40.0])
def test_delivered_quantity_is_conserved_and_never_exceeds_capacity(capacity: float) -> None:
    quotes = [Quote("a", 5.0, 9.0), Quote("b", 16.0, 4.0), Quote("c", 7.0, 6.0)]
    served, _ = clear(quotes, capacity)
    total = sum(served.values())
    assert total == pytest.approx(min(capacity, sum(quote.quantity for quote in quotes)))
    assert total <= capacity + 1e-9


def test_equal_values_are_ordered_by_key_so_the_outcome_is_deterministic() -> None:
    quotes = [Quote("b", 1.0, 5.0), Quote("a", 1.0, 5.0)]
    served, _ = clear(quotes, 1.0)
    assert served == {"a": 1.0, "b": 0.0}
    # Reversing the input must not reverse the winner.
    assert clear(list(reversed(quotes)), 1.0)[0] == {"a": 1.0, "b": 0.0}


def test_zero_quantity_bids_take_no_capacity_and_set_no_shadow_price() -> None:
    quotes = [Quote("phantom", 0.0, 99.0), Quote("real", 5.0, 3.0)]
    served, shadow = clear(quotes, 10.0)
    assert served == {"phantom": 0.0, "real": 5.0}
    assert shadow == 0.0
    # With no capacity at all the panic price still ignores the phantom bid.
    assert clear(quotes, 0.0)[1] == 3.0


def test_a_fully_used_capacity_reports_the_value_of_the_last_unit_served() -> None:
    quotes = [Quote("a", 5.0, 7.0), Quote("b", 5.0, 2.0)]
    _, shadow = clear(quotes, 10.0)
    assert shadow == 2.0


def test_no_bids_clear_without_a_price() -> None:
    assert clear([], 5.0) == ({}, 0.0)


def test_non_positive_capacity_serves_nothing_but_still_quotes_the_panic_price() -> None:
    quotes = [Quote("a", 1.0, 4.0), Quote("b", 2.0, 8.0)]
    assert clear(quotes, -1.0) == ({"a": 0.0, "b": 0.0}, 8.0)


def test_denial_is_measured_across_a_crops_whole_step_curve() -> None:
    """A crop files three bids per resource; only its first one is served.

    Reading one step out of a dict keyed by crop — the obvious implementation,
    and the one that shipped first — reports zero denial here, understating
    ``pressure`` for every crop that lost the auction.  This case is the
    regression.
    """
    quotes = [
        Quote("c", 4.0, 3.0),
        Quote("c", 4.0, 2.0),
        Quote("c", 4.0, 1.0),
        Quote("t", 2.0, 9.0),
    ]
    served, _ = clear(quotes, 6.0)
    assert served == {"c": 4.0, "t": 2.0}

    requested, denied = denied_by_crop(quotes, served)
    assert requested == {"c": 12.0, "t": 2.0}
    assert denied == {"c": 8.0, "t": 0.0}


def test_denial_is_zero_when_every_step_is_served() -> None:
    quotes = [Quote("a", 4.0, 5.0), Quote("a", 4.0, 4.0)]
    served, _ = clear(quotes, 100.0)
    _, denied = denied_by_crop(quotes, served)
    assert denied == {"a": 0.0}


def test_denial_never_goes_negative_when_rounding_serves_more_than_requested() -> None:
    _, denied = denied_by_crop([Quote("a", 1.0, 1.0)], {"a": 1.0 + 1e-12})
    assert denied == {"a": 0.0}


def test_a_crop_missing_from_the_allocation_has_its_whole_request_denied() -> None:
    quotes = [Quote("a", 2.0, 5.0), Quote("b", 3.0, 1.0)]
    _, denied = denied_by_crop(quotes, {"a": 2.0})
    assert denied == {"a": 0.0, "b": 3.0}


def test_denial_sums_to_the_unserved_remainder() -> None:
    quotes = [Quote("a", 5.0, 9.0), Quote("a", 5.0, 6.0), Quote("b", 30.0, 4.0)]
    served, _ = clear(quotes, 12.0)
    requested, denied = denied_by_crop(quotes, served)
    assert sum(denied.values()) == pytest.approx(sum(requested.values()) - sum(served.values()))


def test_no_bids_produce_no_denial() -> None:
    assert denied_by_crop([], {}) == ({}, {})
