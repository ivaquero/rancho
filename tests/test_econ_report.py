"""Regression tests for the economics HTML report and its serialisation.

The report is the only artefact a reader actually sees, so a wrong colour, an
inverted axis or a bogus "0 元/kg" is a wrong conclusion even when every
underlying number is right.  These tests render real probe output rather than
mocking the writers, so a change in either the data shape or the markup shows
up here.
"""

from __future__ import annotations

import re
from dataclasses import replace

import pytest
from support import make_arm, make_snapshot

from rancho.econ.probe import _compare, probe
from rancho.econ.report import GROWTH_METHODS, _chart, _delta_class, render_html


@pytest.fixture(scope="module")
def result():
    """A small real run: enough records and hours to exercise every figure."""
    return probe(days=2, tariff_keys=("sv_horaria",), growth="cropforge")


@pytest.mark.parametrize(
    ("value", "higher_is_better", "expected"),
    [
        (0.1, False, "bad"),  # unit cost up: worse
        (-0.1, False, "good"),  # unit cost down: better
        (0.1, True, "good"),  # yield / margin up: better
        (-0.1, True, "bad"),  # yield / margin down: worse
        (0.0, True, ""),
    ],
)
def test_delta_colour_follows_the_metric_not_the_sign(
    value: float, higher_is_better: bool, expected: str
) -> None:
    assert _delta_class(value, higher_is_better=higher_is_better) == expected


def test_a_constant_negative_series_keeps_an_ascending_axis() -> None:
    """A flat -5 line used to render ticks from -5.0 down to -5.3."""
    svg = _chart([("x", [-5.0, -5.0], "#000", None)], ticks=[], y_fmt="{:.2f}")
    ticks = [float(text) for text in re.findall(r'class="tick">(-?[\d.]+)</text>', svg)]
    assert ticks == sorted(ticks), f"y-axis ticks must ascend, got {ticks}"
    assert ticks[-1] > ticks[0], "a constant series still needs a non-zero span"


def test_the_method_note_matches_the_selected_growth_backend(result) -> None:
    for growth in GROWTH_METHODS:
        markup = render_html(replace(result, growth=growth), title="测试")
        assert GROWTH_METHODS[growth][:12] in markup, f"{growth} note missing"
    # Backends must not leak into each other's description.
    analytic = render_html(replace(result, growth="analytic"), title="测试")
    assert "农场季真跑" not in analytic


def test_a_zero_yield_snapshot_publishes_null_not_zero() -> None:
    snapshot = make_snapshot("tomato", yield_kg=0.0, cost_total=8.9179)
    assert snapshot.as_dict()["unit_cost"] is None
    healthy = make_snapshot("tomato", yield_kg=10.0, cost_total=8.9179)
    assert healthy.as_dict()["unit_cost"] == pytest.approx(0.8918, abs=1e-4)


def test_a_zero_yield_comparison_is_marked_incomparable() -> None:
    solo = make_arm("solo", [make_snapshot("tomato", yield_kg=0.0, cost_total=8.9)])
    pooled = make_arm("pooled", [make_snapshot("tomato", yield_kg=0.0, cost_total=8.9)])
    (item,) = _compare(solo, pooled)
    assert item.comparable is False
    payload = item.as_dict()
    assert payload["solo_unit_cost"] is None
    assert payload["pooled_unit_cost"] is None
    assert payload["delta_unit_pct"] is None


def test_the_report_shows_a_dash_for_an_incomparable_row(result) -> None:
    regime = result.regimes[0]
    original = regime.comparisons[0]
    incomparable = replace(
        original,
        comparable=False,
        solo_unit_cost=0.0,
        pooled_unit_cost=0.0,
        delta_unit=0.0,
    )
    regime = replace(regime, comparisons=(incomparable, *regime.comparisons[1:]))
    markup = render_html(replace(result, regimes=(regime,)), title="测试")
    assert "<td>—</td>" in markup
    assert "绝收" in markup  # the note explains why the row is blank
    assert 'class="bad"' in markup or 'class="good"' in markup


def test_the_report_renders_a_complete_self_contained_document(result) -> None:
    markup = render_html(result, title="回归测试")
    assert markup.startswith("<!doctype html>")
    for heading in (
        "输入序列",
        "累计成本曲线",
        "单位成本曲线",
        "成本构成",
        "逐作物对照",
        "稀缺时刻",
    ):
        assert heading in markup
    assert "三臂含义" in markup
    assert "<table>" in markup
    # No external assets: the promise in the module docstring.
    assert "<script" not in markup
    assert "http://" not in markup and "https://" not in markup
