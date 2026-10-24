"""The controlled experiment that decides whether the coupling is sound.

A cost curve is only evidence of *competition* if the same forcing without
competition produces a different curve.  So the probe runs three arms on
identical weather, prices and seed:

``solo``
    Capacity multiplied by 10⁴.  Each crop buys whatever it wants; the curve is
    the counterfactual "if this crop had the house to itself".
``pooled``
    Real capacities.  The three crops clear against one another.
``null``
    Capacity multiplied by 10³.  This must reproduce ``solo`` to floating-point
    precision; if it does not, the allocator itself is biasing the result and
    nothing downstream can be trusted.

The verdict then asks two questions, in order: *did capacity bind*, and *did
the binding change the crops differently*.  Only when both are true is the
difference attributable to competition rather than to price or weather.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from rancho.econ.farm import MAX_PASSES, Coupling, FarmSeason
from rancho.econ.model import NULL_SCALE, SOLO_SCALE, ArmResult, run_arm
from rancho.econ.spec import CROPS, SITES, TARIFFS, CropSpec, SiteProfile, TariffSpec

#: Relative tolerance for the null test.  The two arms perform the same
#: arithmetic in the same order, so anything above rounding noise is a bug.
NULL_TOLERANCE = 1e-9
#: A crop is "differentiated" when its unit cost moves by more than this.
MIN_UNIT_SHIFT = 0.02
#: The run counts as capacity-constrained when this share of demand is denied.
MIN_PRESSURE = 0.05


@dataclass(frozen=True, slots=True)
class CropComparison:
    """Solo-versus-pooled deltas for one crop."""

    key: str
    label: str
    solo_unit_cost: float
    pooled_unit_cost: float
    delta_unit: float
    solo_yield: float
    pooled_yield: float
    delta_yield: float
    denied_kwh: float
    denied_share: float
    pressure: float
    solo_margin: float
    pooled_margin: float
    delta_margin: float
    #: Absolute change in margin.  A bed that barely broke even has a margin
    #: near zero, so the ratio is meaningless there and the report quotes the
    #: yuan difference instead — see :attr:`margin_flat`.
    delta_margin_yuan: float
    margin_flat: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "solo_unit_cost": round(self.solo_unit_cost, 4),
            "pooled_unit_cost": round(self.pooled_unit_cost, 4),
            "delta_unit_pct": round(self.delta_unit * 100.0, 2),
            "solo_yield_kg": round(self.solo_yield, 2),
            "pooled_yield_kg": round(self.pooled_yield, 2),
            "delta_yield_pct": round(self.delta_yield * 100.0, 2),
            "denied_kwh": round(self.denied_kwh, 2),
            "denied_share": round(self.denied_share, 4),
            "pressure": round(self.pressure, 4),
            "solo_margin_yuan": round(self.solo_margin, 2),
            "pooled_margin_yuan": round(self.pooled_margin, 2),
            "delta_margin_pct": round(self.delta_margin * 100.0, 2),
            "delta_margin_yuan": round(self.delta_margin_yuan, 2),
            "margin_flat": self.margin_flat,
        }


@dataclass(frozen=True, slots=True)
class Verdict:
    """Whether the arm comparison supports the competition hypothesis."""

    null_ok: bool
    null_gap: float
    pressure: float
    average_pressure: float
    binding_crop: str
    spread: float
    rho: float
    differentiated: bool
    passed: bool
    summary: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "null_ok": self.null_ok,
            "null_gap": self.null_gap,
            "pressure": round(self.pressure, 4),
            "average_pressure": round(self.average_pressure, 4),
            "binding_crop": self.binding_crop,
            "unit_cost_spread": round(self.spread, 4),
            "rank_correlation": round(self.rho, 4),
            "differentiated": self.differentiated,
            "passed": self.passed,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class RegimeResult:
    """One tariff regime, all three arms."""

    tariff: TariffSpec
    solo: ArmResult
    pooled: ArmResult
    null: ArmResult
    comparisons: tuple[CropComparison, ...]
    verdict: Verdict


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """The whole experiment, ready to serialise."""

    id: str
    generated_at: str
    site: SiteProfile
    days: int
    seed: int
    regimes: tuple[RegimeResult, ...]
    #: Which production function produced the yields: ``analytic`` (the phase-1
    #: saturating proxy), ``cropforge`` (interception on a phenology-driven
    #: canopy) or ``farm`` (a real CropForge season, coupled back into the
    #: market).  Recorded because the three do not agree on cost per kg.
    growth: str = "analytic"
    #: How the coupled backend converged, when one was used: pass count,
    #: residual, grid size.  ``None`` for the single-shot backends.
    coupling: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "generated_at": self.generated_at,
            "site": {
                "key": self.site.key,
                "label": self.site.label,
                "latitude": self.site.latitude,
                "mean_temp_c": self.site.mean_temp_c,
                "cover_transmission": self.site.cover_transmission,
            },
            "days": self.days,
            "seed": self.seed,
            "growth": self.growth,
            "coupling": self.coupling,
            "regimes": [
                {
                    "tariff": {
                        "key": regime.tariff.key,
                        "label": regime.tariff.label,
                        "valley": regime.tariff.valley,
                        "flat": regime.tariff.flat,
                        "peak": regime.tariff.peak,
                    },
                    "forcing": regime.pooled.forcing.summary(),
                    "arms": {
                        arm.arm: {
                            "cap_scale": arm.cap_scale,
                            "total_cost_yuan": round(arm.total_cost(), 3),
                            "total_yield_kg": round(arm.total_yield(), 3),
                            "crops": [crop.as_dict() for crop in arm.crops],
                        }
                        for arm in (regime.solo, regime.pooled, regime.null)
                    },
                    "comparisons": [item.as_dict() for item in regime.comparisons],
                    "verdict": regime.verdict.as_dict(),
                    "series": _series_payload(regime),
                }
                for regime in self.regimes
            ],
        }


def _series_payload(regime: RegimeResult) -> dict[str, Any]:
    """Daily cumulative cost and unit cost, per arm and crop."""
    payload: dict[str, Any] = {}
    for arm in (regime.pooled, regime.solo):
        for record in arm.records:
            entry = payload.setdefault(
                record.crop,
                {"pooled_cost": [], "solo_cost": [], "pooled_unit": [], "solo_unit": []},
            )
            key = "pooled" if arm.arm == "pooled" else "solo"
            entry[f"{key}_cost"].append(record.cost_cum)
            entry[f"{key}_unit"].append(record.unit_cost)
    return payload


def probe(
    *,
    site_key: str = "tropical",
    days: int = 28,
    seed: int = 42,
    tariff_keys: tuple[str, ...] = ("farm", "cheap"),
    crop_keys: tuple[str, ...] = ("tomato", "cucumber", "lettuce"),
    growth: str = "cropforge",
    passes: int = MAX_PASSES,
    season: FarmSeason | None = None,
) -> ProbeResult:
    """Run every tariff regime under the three arms and score the outcome.

    ``growth="farm"`` closes the loop between the two layers: the market clears,
    the resulting season is handed to a real CropForge field, the measured light
    response becomes the next pass's bid curve, and the loop repeats until the
    total yield stops moving.
    """
    site = SITES[site_key]
    crops: tuple[CropSpec, ...] = tuple(CROPS[key] for key in crop_keys)
    coupled = growth == "farm"
    setup = season if season is not None else FarmSeason()
    coupling: dict[str, Any] | None = None

    regimes: list[RegimeResult] = []
    for tariff_key in tariff_keys:
        tariff = TARIFFS[tariff_key]
        if coupled:
            solo, pooled, null, coupling = _coupled_arms(
                site=site,
                tariff=tariff,
                crops=crops,
                days=days,
                seed=seed,
                setup=setup,
                passes=passes,
            )
        else:
            solo, pooled, null = _single_shot_arms(
                site=site, tariff=tariff, crops=crops, days=days, seed=seed, growth=growth
            )
        comparisons = _compare(solo, pooled)
        verdict = _verdict(solo, pooled, null, comparisons)
        regimes.append(
            RegimeResult(
                tariff=tariff,
                solo=solo,
                pooled=pooled,
                null=null,
                comparisons=comparisons,
                verdict=verdict,
            )
        )

    digest = hashlib.sha1(
        json.dumps(
            {
                "site": site_key,
                "days": days,
                "seed": seed,
                "tariffs": list(tariff_keys),
                "crops": list(crop_keys),
                "growth": growth,
                "passes": passes if coupled else None,
                "farm": {"grid": setup.grid} if coupled else None,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]

    return ProbeResult(
        id=digest,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        site=site,
        days=days,
        seed=seed,
        regimes=tuple(regimes),
        growth=growth,
        coupling=coupling,
    )


def _single_shot_arms(
    *,
    site: SiteProfile,
    tariff: TariffSpec,
    crops: tuple[CropSpec, ...],
    days: int,
    seed: int,
    growth: str,
) -> tuple[ArmResult, ArmResult, ArmResult]:
    """The three arms with one production function, evaluated once."""
    solo = run_arm(
        site=site,
        tariff=tariff,
        crops=crops,
        days=days,
        seed=seed,
        arm="solo",
        cap_scale=SOLO_SCALE,
        growth=growth,
    )
    pooled = run_arm(
        site=site,
        tariff=tariff,
        crops=crops,
        days=days,
        seed=seed,
        arm="pooled",
        growth=growth,
    )
    null = run_arm(
        site=site,
        tariff=tariff,
        crops=crops,
        days=days,
        seed=seed,
        arm="null",
        cap_scale=NULL_SCALE,
        growth=growth,
    )
    return solo, pooled, null


def _coupled_arms(
    *,
    site: SiteProfile,
    tariff: TariffSpec,
    crops: tuple[CropSpec, ...],
    days: int,
    seed: int,
    setup: FarmSeason,
    passes: int,
) -> tuple[ArmResult, ArmResult, ArmResult, dict[str, Any]]:
    """Alternate market clearing and Farm seasons until the yields settle.

    The ``null`` arm is fed the ``solo`` response curve on purpose: it tests the
    *allocator*, not the physics, and giving both arms the same curve is what
    keeps the two runs bit-comparable.
    """
    loop = Coupling(setup=setup, passes=passes)
    solo: ArmResult | None = None
    pooled: ArmResult | None = None
    null: ArmResult | None = None
    for index in range(max(1, passes)):
        solo = run_arm(
            site=site,
            tariff=tariff,
            crops=crops,
            days=days,
            seed=seed,
            arm="solo",
            cap_scale=SOLO_SCALE,
            growth=loop.factory("solo"),
        )
        pooled = run_arm(
            site=site,
            tariff=tariff,
            crops=crops,
            days=days,
            seed=seed,
            arm="pooled",
            growth=loop.factory("pooled"),
        )
        null = run_arm(
            site=site,
            tariff=tariff,
            crops=crops,
            days=days,
            seed=seed,
            arm="null",
            cap_scale=NULL_SCALE,
            growth=loop.factory("solo"),
        )
        settled = loop.record(index, {"solo": solo.total_yield(), "pooled": pooled.total_yield()})
        if settled or index == passes - 1:
            break
        loop.refit("solo", crops, solo)
        loop.refit("pooled", crops, pooled)

    if solo is None or pooled is None or null is None:  # pragma: no cover - passes >= 1
        raise RuntimeError("the coupled loop produced no arms")
    return solo, pooled, null, loop.summary()


def _compare(solo: ArmResult, pooled: ArmResult) -> tuple[CropComparison, ...]:
    """Per-crop unit-cost and yield deltas, plus how much each crop lost."""
    solo_by = solo.by_crop()
    total_denied = sum(crop.kwh_hvac_denied + crop.kwh_light_denied for crop in pooled.crops) or 1.0

    comparisons: list[CropComparison] = []
    for crop in pooled.crops:
        base = solo_by[crop.key]
        denied = crop.kwh_hvac_denied + crop.kwh_light_denied
        requested = denied + crop.kwh_hvac_thermal + crop.kwh_light
        comparisons.append(
            CropComparison(
                key=crop.key,
                label=crop.label,
                solo_unit_cost=base.unit_cost,
                pooled_unit_cost=crop.unit_cost,
                delta_unit=_relative(crop.unit_cost, base.unit_cost),
                solo_yield=base.yield_kg,
                pooled_yield=crop.yield_kg,
                delta_yield=_relative(crop.yield_kg, base.yield_kg),
                denied_kwh=denied,
                denied_share=denied / total_denied,
                pressure=denied / requested if requested > 0.0 else 0.0,
                solo_margin=base.margin_yuan,
                pooled_margin=crop.margin_yuan,
                delta_margin=_relative(crop.margin_yuan, base.margin_yuan),
                delta_margin_yuan=crop.margin_yuan - base.margin_yuan,
                # Below a tenth of revenue the ratio says more about the
                # denominator than about the change.
                margin_flat=abs(base.margin_yuan) < 0.10 * max(1e-9, base.revenue_yuan),
            )
        )
    return tuple(comparisons)


def _verdict(
    solo: ArmResult,
    pooled: ArmResult,
    null: ArmResult,
    comparisons: tuple[CropComparison, ...],
) -> Verdict:
    """Score the comparison: allocator sane, capacity tight, effects uneven."""
    solo_by = solo.by_crop()
    null_gap = 0.0
    for crop in null.crops:
        base = solo_by[crop.key]
        null_gap = max(
            null_gap,
            abs(_relative(crop.unit_cost, base.unit_cost)),
            abs(_relative(crop.yield_kg, base.yield_kg)),
        )

    deltas = [item.delta_unit for item in comparisons]
    spread = max(deltas) - min(deltas) if deltas else 0.0
    # Weighted denial rate across the two capacity-constrained channels.  The
    # units differ (kWh_th vs kWh_e) but both are "energy the crop asked for
    # and did not get", which is what the pressure metric is about.
    # A house is capacity-constrained when *any* crop is being denied.  The
    # house-wide average would hide a starved bed behind two satisfied ones,
    # which is precisely the asymmetry the probe is looking for.
    requested = sum(
        crop.kwh_hvac_denied + crop.kwh_light_denied + crop.kwh_hvac_thermal + crop.kwh_light
        for crop in pooled.crops
    )
    denied = sum(crop.kwh_hvac_denied + crop.kwh_light_denied for crop in pooled.crops)
    average_pressure = denied / requested if requested > 0.0 else 0.0
    pressure = max((item.pressure for item in comparisons), default=0.0)

    top = max(comparisons, key=lambda item: item.pressure) if comparisons else None
    binding_crop = top.label if top is not None else "—"
    rho = _spearman(
        [item.denied_share for item in comparisons],
        [item.delta_unit for item in comparisons],
    )
    differentiated = spread > MIN_UNIT_SHIFT and max(map(abs, deltas), default=0.0) > MIN_UNIT_SHIFT
    null_ok = null_gap <= NULL_TOLERANCE
    passed = null_ok and differentiated and pressure > MIN_PRESSURE

    if not null_ok:
        summary = "分配器自检未通过：无约束臂与 solo 臂结果不一致，先修代码。"
    elif pressure <= MIN_PRESSURE:
        summary = f"容量基本不紧（最紧作物逾配 {pressure:.1%}），本轮看不到竞争，需收紧容量再跑。"
    elif not differentiated:
        summary = (
            f"容量收紧但各作物单位成本几乎同步变化（极差 {spread:.1%}），"
            "说明配额等于按比例分摊，未产生差异化。"
        )
    else:
        share = top.denied_share if top is not None else 0.0
        summary = (
            f"通过：缺口集中在{binding_crop}（逾配 {pressure:.0%}，占总超配 {share:.0%}），"
            f"单位成本变动极差 {spread:.1%}，秩相关 {rho:+.2f}——"
            "竞争按边际价值排序，而不是平摊。"
        )

    return Verdict(
        null_ok=null_ok,
        null_gap=null_gap,
        pressure=pressure,
        average_pressure=average_pressure,
        binding_crop=binding_crop,
        spread=spread,
        rho=rho,
        differentiated=differentiated,
        passed=passed,
        summary=summary,
    )


def _relative(value: float, base: float) -> float:
    return (value - base) / base if abs(base) > 1e-12 else 0.0


def _spearman(xs: list[float], ys: list[float]) -> float:
    """Rank correlation with average ranks for ties, without SciPy."""
    if len(xs) < 2:
        return 0.0
    rx = _ranks(xs)
    ry = _ranks(ys)
    mx = sum(rx) / len(rx)
    my = sum(ry) / len(ry)
    num = sum((x - mx) * (y - my) for x, y in zip(rx, ry, strict=True))
    dx = math.sqrt(sum((x - mx) ** 2 for x in rx))
    dy = math.sqrt(sum((y - my) ** 2 for y in ry))
    return num / (dx * dy) if dx > 0.0 and dy > 0.0 else 0.0


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index
        while end + 1 < len(order) and math.isclose(
            values[order[end + 1]], values[order[index]], rel_tol=1e-12
        ):
            end += 1
        average = (index + end) / 2.0 + 1.0
        for position in range(index, end + 1):
            ranks[order[position]] = average
        index = end + 1
    return ranks
