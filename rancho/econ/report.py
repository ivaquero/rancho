"""Writers for the economics probe: JSON, CSV, and a self-contained HTML report.

The report exists because a cost *curve* is the thing being validated.  A table
of end-of-run totals cannot show whether two crops diverged gradually or jumped
on one bad afternoon, so the HTML carries the daily series as inline SVG.  No
external assets, no CDN, no JavaScript: the file opens anywhere and stays
readable in five years.
"""

from __future__ import annotations

import csv
import html
import json
from collections.abc import Callable
from operator import attrgetter
from pathlib import Path
from typing import Any, Final

from rancho.econ.forcing import Forcing
from rancho.econ.probe import CropComparison, ProbeResult, RegimeResult
from rancho.econ.records import DayRecord
from rancho.econ.spec import HOURS_PER_DAY

#: Categorical palette, one per crop.  Chosen to stay distinguishable in
#: grayscale print as well as on screen.
#: How the production function is described in the report header.
GROWTH_LABELS: dict[str, str] = {
    "analytic": "解析代理 · 饱和光响应",
    "cropforge": "CropForge 插件 · Beer-Lambert 截获",
    "farm": "CropForge 农场 · 市场与物理迭代",
}

CROP_COLORS: Final[dict[str, str]] = {
    "tomato": "#c2410c",
    "cucumber": "#15803d",
    "lettuce": "#0e7490",
}
FALLBACK_COLORS: Final[tuple[str, ...]] = ("#7c3aed", "#b45309", "#0369a1")

COST_COLORS: Final[dict[str, str]] = {
    "light": "#f2b23c",
    "hvac": "#3f7fbf",
    "water": "#4fa3a5",
    "fert": "#9a7bb8",
    "shared": "#c9d1cb",
}
COST_LABELS: Final[dict[str, str]] = {
    "light": "人工光源",
    "hvac": "温控电耗",
    "water": "水费与泵耗",
    "fert": "肥料",
    "shared": "公共电耗",
}

CSS: Final = """
:root{--ink:#1f2a24;--muted:#68766e;--line:#e3e8e4;--bg:#fbfcfa;--card:#ffffff;
--accent:#2f6f4f;--warn:#b45309;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.65 ui-sans-serif,-apple-system,"Segoe UI","Noto Sans SC",sans-serif}
main{max-width:1080px;margin:0 auto;padding:40px 28px 88px}
h1{font-size:26px;line-height:1.3;margin:0 0 6px;letter-spacing:-.01em}
h2{font-size:17px;margin:44px 0 14px;padding-bottom:8px;border-bottom:1px solid var(--line)}
h3{font-size:14px;margin:26px 0 8px;color:var(--muted);font-weight:600;
letter-spacing:.04em;text-transform:uppercase}
p{margin:8px 0}
.lede{color:var(--muted);margin:0 0 24px;max-width:62ch}
.meta{display:flex;flex-wrap:wrap;gap:6px 18px;color:var(--muted);font-size:12.5px;
margin-bottom:26px}
.meta b{color:var(--ink);font-weight:600}
.verdict{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--accent);
border-radius:8px;padding:16px 18px;margin:14px 0 8px}
.verdict.fail{border-left-color:var(--warn)}
.verdict .tag{display:inline-block;font-size:11.5px;font-weight:700;letter-spacing:.08em;
text-transform:uppercase;color:var(--accent);margin-bottom:6px}
.verdict.fail .tag{color:var(--warn)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.card .k{font-size:11.5px;color:var(--muted);letter-spacing:.04em}
.card .v{font-size:20px;font-weight:650;margin-top:2px;font-variant-numeric:tabular-nums}
.card .u{font-size:11.5px;color:var(--muted);margin-left:3px;font-weight:400}
figure{margin:16px 0 22px;background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:14px 12px 6px}
figcaption{font-size:12px;color:var(--muted);padding:2px 4px 8px}
svg{display:block;width:100%;height:auto}
.tick{font-size:10.5px;fill:var(--muted)}
.axlabel{font-size:11px;fill:var(--muted)}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12px;color:var(--muted);
padding:2px 6px 10px}
.legend i{display:inline-block;width:16px;height:0;border-top-width:2px;border-top-style:solid;
vertical-align:middle;margin-right:6px}
table{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0 6px;
font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:7px 9px;border-bottom:1px solid var(--line)}
th:first-child,td:first-child{text-align:left}
thead th{font-size:11.5px;color:var(--muted);font-weight:600;letter-spacing:.03em;
text-transform:uppercase;border-bottom:1px solid #cfd8d2}
tbody tr:hover{background:#f4f7f4}
.pos{color:#b91c1c}.neg{color:#15803d}
.note{font-size:12.5px;color:var(--muted);max-width:70ch}
code{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;background:#eef2ee;
padding:1px 5px;border-radius:4px}
footer{margin-top:56px;padding-top:18px;border-top:1px solid var(--line);
font-size:12px;color:var(--muted)}
"""


def write_json(result: ProbeResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def write_csv(result: ProbeResult, path: Path) -> Path:
    """One row per regime × arm × crop × day — the flat form for spreadsheets."""
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = (
        "regime",
        "arm",
        *DayRecord.__dataclass_fields__.keys(),
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns))
        writer.writeheader()
        for regime in result.regimes:
            for arm in (regime.solo, regime.null, regime.pooled):
                for record in arm.records:
                    writer.writerow(
                        {"regime": regime.tariff.key, "arm": arm.arm, **record.as_dict()}
                    )
    return path


def write_html(result: ProbeResult, path: Path, *, title: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(result, title=title), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# HTML assembly
# ---------------------------------------------------------------------------


def render_html(result: ProbeResult, *, title: str) -> str:
    """Build the whole document as one string."""
    body = [
        f"<h1>{html.escape(title)}</h1>",
        (
            '<p class="lede">一个「大棚 Agent」持有共享容量，三个「作物 Agent」按时逐小时竞价。'
            "报告给出同一组气象与电价下，独享容量（solo）与共享容量（pooled）两条轨迹的差别，"
            "以及分配器自检（null）是否与 solo 逐位一致。</p>"
        ),
        _meta_block(result),
    ]
    for regime in result.regimes:
        body.append(_regime_section(regime))
    body.append(_method_footer())
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head>"
        f"<body><main>{''.join(body)}</main></body></html>"
    )


def _meta_block(result: ProbeResult) -> str:
    summary = result.regimes[0].pooled.forcing.summary() if result.regimes else {}
    items = [
        ("站点", result.site.label),
        ("天数", f"{result.days} d · {result.days * 24} h"),
        ("随机种子", str(result.seed)),
        ("产量来源", GROWTH_LABELS.get(result.growth, result.growth)),
        ("平均气温", f"{summary.get('mean_temp_c', 0):.1f} °C"),
        ("日均 DLI", f"{summary.get('mean_dli', 0):.1f} mol/m²"),
    ]
    if result.coupling is not None:
        loop = result.coupling
        state = "收敛" if loop["converged"] else "达到轮数上限"
        items.append(
            (
                "双层迭代",
                f"{loop['passes']} 轮 · 残差 {loop['residual'] * 100:.3f}% · "
                f"{loop['grid']}×{loop['grid']} 网格 · {state}",
            )
        )
    items.append(("运行编号", result.id))
    chips = "".join(f"<span>{html.escape(k)} <b>{html.escape(v)}</b></span>" for k, v in items)
    return f'<div class="meta">{chips}</div>'


def _regime_section(regime: RegimeResult) -> str:
    tariff = regime.tariff
    verdict = regime.verdict
    classes = "verdict" if verdict.passed else "verdict fail"
    tag = "验证通过" if verdict.passed else "未通过"
    cards = _cards(regime)

    parts = [
        f"<h2>情景 · {html.escape(tariff.label)}</h2>",
        (
            '<p class="note">峰 '
            f"{tariff.peak:.4f} / 平 {tariff.flat:.4f} / 谷 {tariff.valley:.4f} "
            f"{tariff.currency}·kWh⁻¹ ≈ "
            f"{tariff.peak * tariff.fx:.2f} / {tariff.flat * tariff.fx:.2f} / "
            f"{tariff.valley * tariff.fx:.2f} 元·kWh⁻¹，"
            "叠加每日市场化系数；水价与肥料价各自按随机游走漂移。</p>"
        ),
        f'<div class="{classes}"><span class="tag">{tag}</span>'
        f"<div>{html.escape(verdict.summary)}</div></div>",
        cards,
        "<h3>输入序列</h3>",
        _forcing_figures(regime.pooled.forcing),
        "<h3>累计成本曲线</h3>",
        _cost_figure(regime),
        "<h3>单位成本曲线</h3>",
        _unit_cost_figure(regime),
        "<h3>成本构成</h3>",
        _composition_figure(regime),
        "<h3>逐作物对照</h3>",
        _comparison_table(regime),
        "<h3>稀缺时刻</h3>",
        _scarcity_figure(regime),
    ]
    return "".join(parts)


def _cards(regime: RegimeResult) -> str:
    pooled = regime.pooled
    verdict = regime.verdict
    total = pooled.total_cost()
    yield_kg = pooled.total_yield()
    items = [
        ("总可变成本", f"{total:,.0f}", "元"),
        ("折产量", f"{yield_kg:,.0f}", "kg"),
        ("平均单位成本", f"{total / yield_kg:.2f}" if yield_kg else "—", "元/kg"),
        ("最紧作物逾配", f"{verdict.pressure * 100:.0f}", "%"),
        ("单位成本变动极差", f"{verdict.spread * 100:.1f}", "%"),
        ("分配器自检误差", f"{verdict.null_gap:.1e}", ""),
    ]
    return (
        '<div class="cards">'
        + "".join(
            f'<div class="card"><div class="k">{html.escape(k)}</div>'
            f'<div class="v">{html.escape(v)}<span class="u">{html.escape(u)}</span></div></div>'
            for k, v, u in items
        )
        + "</div>"
    )


def _forcing_figures(forcing: Forcing) -> str:
    """Daily temperature envelope plus one week of the hourly tariff."""
    days = list(range(forcing.days))
    means = [forcing.mean_temp(day) for day in days]
    highs = [max(forcing.temp_out[hour] for hour in forcing.day_hours(day)) for day in days]
    lows = [min(forcing.temp_out[hour] for hour in forcing.day_hours(day)) for day in days]

    week = min(7, forcing.days)
    prices = list(forcing.tariff_price[: week * HOURS_PER_DAY])
    return _figure(
        _chart(
            [
                ("日最高", highs, "#c2cbc5", None),
                ("日均", means, "#1f2a24", None),
                ("日最低", lows, "#c2cbc5", None),
            ],
            ticks=_day_ticks(forcing.days),
            y_fmt="{:.0f}",
        ),
        "室外气温包络（°C）：日均与当日最低/最高。棚内被动温度 = 室外 + 太阳得热，"
        "这是温控负荷的来源。",
    ) + _figure(
        _chart(
            [("到户电价", prices, "#b45309", None)],
            ticks=[(day * HOURS_PER_DAY, str(day + 1)) for day in range(week)],
            y_fmt="{:.2f}",
        ),
        "前 7 天的逐小时到户电价（元/kWh）：峰谷分时形状 × 每日市场化系数。"
        "补光只有在谷段才可能跨过盈亏线。",
    )


def _cost_figure(regime: RegimeResult) -> str:
    return _paired_figure(
        regime,
        attrgetter("cost_cum"),
        y_fmt="{:.0f}",
        caption="累计可变成本（元）。实线为共享容量，虚线为同一作物独享全部容量；"
        "两条线的斜率差就是竞争造成的边际成本差。",
    )


def _unit_cost_figure(regime: RegimeResult) -> str:
    return _paired_figure(
        regime,
        attrgetter("unit_cost"),
        y_fmt="{:.1f}",
        caption="累计单位成本（元/kg 折产量）。前期为零是因为首日尚无产量，曲线从中段起可读。",
    )


def _paired_figure(
    regime: RegimeResult,
    value: Callable[[DayRecord], float],
    *,
    y_fmt: str,
    caption: str,
) -> str:
    """One metric per crop, shared versus solo: the shape every curve here has."""
    series: list[tuple[str, list[float], str, str | None]] = []
    for crop in regime.pooled.crops:
        color = _color(crop.key)
        for arm, suffix, dash in (
            (regime.pooled, "共享", None),
            (regime.solo, "独享", 'stroke-dasharray="4 3"'),
        ):
            points = [value(row) for row in arm.records if row.crop == crop.key]
            series.append((f"{crop.label} · {suffix}", points, color, dash))
    legend = "".join(
        f'<span><i style="border-top-color:{color};{_dash_style(dash)}"></i>{html.escape(label)}</span>'
        for label, _values, color, dash in series
    )
    return _figure(
        f'<div class="legend">{legend}</div>'
        + _chart(series, ticks=_day_ticks(regime.pooled.days), y_fmt=y_fmt),
        caption,
    )


def _composition_figure(regime: RegimeResult) -> str:
    crops = regime.pooled.crops
    keys = ("light", "hvac", "water", "fert", "shared")
    bars = []
    max_total = max(crop.cost_total for crop in crops) or 1.0
    width, height = 760, 220
    left, top, plot_h = 46, 14, 140
    bar_w = 74
    gap = (width - left - 40 - bar_w * len(crops)) / max(1, len(crops) - 1)
    for index, crop in enumerate(crops):
        x = left + index * (bar_w + gap)
        values = {
            "light": crop.cost_light,
            "hvac": crop.cost_hvac,
            "water": crop.cost_water,
            "fert": crop.cost_fert,
            "shared": crop.cost_overhead,
        }
        cursor = top + plot_h
        segments = []
        for key in keys:
            if values[key] <= 0:
                continue
            segment_h = values[key] / max_total * plot_h
            cursor -= segment_h
            segments.append(
                f'<rect x="{x:.1f}" y="{cursor:.1f}" width="{bar_w}" height="{segment_h:.1f}" '
                f'fill="{COST_COLORS[key]}" opacity="0.9"/>'
            )
        bars.append("".join(segments))
        bars.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{top + plot_h + 16}" text-anchor="middle" '
            f'class="tick">{html.escape(crop.label)}</text>'
            f'<text x="{x + bar_w / 2:.1f}" y="{top + plot_h + 30}" text-anchor="middle" '
            f'class="tick">{crop.cost_total:.0f} 元</text>'
        )
    axis = (
        f'<line x1="{left - 6}" y1="{top + plot_h}" x2="{width - 20}" y2="{top + plot_h}" '
        'stroke="#cfd8d2"/>'
    )
    legend = "".join(
        f'<span><i style="border-top-color:{COST_COLORS[key]};border-top-width:8px"></i>'
        f"{COST_LABELS[key]}</span>"
        for key in keys
    )
    svg = (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="成本构成">'
        f"{axis}{''.join(bars)}</svg>"
    )
    return _figure(
        f'<div class="legend">{legend}</div>{svg}',
        f"共享容量情景下的成本构成（元，{regime.pooled.days} 天累计）。"
        "同一棚内三项作物的成本结构并不相同，这正是差异化曲线的来源。",
    )


def _margin_change(item: CropComparison) -> str:
    """Margin change as a percentage, or in yuan when the base is ~zero."""
    if item.margin_flat:
        return f"{item.delta_margin_yuan:+.0f} 元"
    return f"{item.delta_margin * 100:+.1f}%"


def _comparison_table(regime: RegimeResult) -> str:
    pooled = regime.pooled.by_crop()
    # Only the CropForge backend carries a canopy, so the LAI column appears
    # only when there is something to read.
    canopy = any(crop.lai > 0.0 for crop in pooled.values())
    head = (
        "<thead><tr><th>作物</th><th>独享单位成本</th><th>共享单位成本</th>"
        "<th>变动</th><th>产量变动</th><th>毛利变动</th><th>逾配率</th>"
        + ("<th>末 LAI</th>" if canopy else "")
        + "</tr></thead>"
    )
    rows = []
    for item in regime.comparisons:
        rows.append(
            "<tr>"
            f"<td>{html.escape(item.label)}</td>"
            f"<td>{item.solo_unit_cost:.2f}</td>"
            f"<td>{item.pooled_unit_cost:.2f}</td>"
            f'<td class="{_sign(item.delta_unit)}">{item.delta_unit * 100:+.1f}%</td>'
            f'<td class="{_sign(item.delta_yield)}">{item.delta_yield * 100:+.1f}%</td>'
            f'<td class="{_sign(item.delta_margin_yuan)}">'
            f"{_margin_change(item)}</td>"
            f"<td>{item.pressure * 100:.0f}%</td>"
            + (f"<td>{pooled[item.key].lai:.2f}</td>" if canopy else "")
            + "</tr>"
        )
    return (
        f"<table>{head}<tbody>{''.join(rows)}</tbody></table>"
        '<p class="note">单位成本是「元/kg 折产量」，不是售价；毛利 = 折产量 × 售价 − 可变成本，'
        "产量尚未实现销售，因此读作「当期应计」。</p>"
    )


def _scarcity_figure(regime: RegimeResult) -> str:
    hours = regime.pooled.hours
    demand = [h.demand_hvac_kw for h in hours]
    served = [h.served_hvac_kw for h in hours]
    shadow = [h.shadow_thermal for h in hours]
    day_ticks = _hour_ticks(regime.pooled.days)
    first = _figure(
        _chart(
            [
                ("申报冷热量", demand, "#94a3b8", 'stroke-dasharray="3 2"'),
                ("实际供给", served, "#2f6f4f", None),
            ],
            ticks=day_ticks,
            y_fmt="{:.0f}",
        ),
        "逐小时温控申报量与供给量（kW_th）。两条线的缝就是被竞争挤掉的部分。",
    )
    second = _figure(
        _chart([("温控影子价格", shadow, "#b45309", None)], ticks=day_ticks, y_fmt="{:.1f}"),
        "温控影子价格（元/kWh_th）：清单最后一单位的中标价值。为 0 表示容量宽松。",
    )
    return first + second


def _method_footer() -> str:
    return (
        "<footer>"
        "<p><b>模型边界。</b>作物生长用温度基数曲线 + 饱和光响应 + FAO-56 根区胁迫的"
        "简化代理，只用于给竞价定价；产量不是本原型的结论，单位是「折产量」。"
        "第二阶段把这里的代理替换为 CropForge 的逐日干物质增量，其余决策逻辑不变。</p>"
        "<p><b>三臂含义。</b>solo = 容量 ×10⁴；pooled = 实际容量；null = 容量 ×10³，"
        "必须与 solo 逐位一致，否则说明分配器本身有偏。</p>"
        "</footer>"
    )


# ---------------------------------------------------------------------------
# SVG helpers
# ---------------------------------------------------------------------------


def _figure(svg: str, caption: str) -> str:
    return f"<figure>{svg}<figcaption>{html.escape(caption)}</figcaption></figure>"


def _chart(
    series: list[tuple[str, list[float], str, str | None]],
    *,
    ticks: list[tuple[int, str]],
    y_fmt: str = "{:.0f}",
    width: int = 760,
    height: int = 210,
) -> str:
    """Line chart with a light grid, rendered as inline SVG."""
    left, right, top, bottom = 52, 14, 12, 26
    plot_w = width - left - right
    plot_h = height - top - bottom

    values = [value for _label, series_values, _color, _dash in series for value in series_values]
    if not values:
        return f'<svg viewBox="0 0 {width} {height}"></svg>'
    low, high = min(values), max(values)
    if high - low < 1e-9:
        high = low + 1.0 if abs(low) < 1e-9 else low * 1.05
    margin = (high - low) * 0.08
    high += margin
    low = 0.0 if min(values) >= 0.0 and low - margin < 0.0 else low - margin
    span = high - low

    count = max(len(series_values) for _label, series_values, _color, _dash in series)
    step_x = plot_w / max(1, count - 1)

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img">']
    for index in range(5):
        value = low + span * index / 4.0
        y = top + plot_h * (1.0 - (value - low) / span)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="#e9eeea"/>'
        )
        parts.append(
            f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" class="tick">'
            f"{y_fmt.format(value)}</text>"
        )

    for index, label in ticks:
        if not 0 <= index < count:
            continue
        x = left + step_x * index
        parts.append(
            f'<text x="{x:.1f}" y="{height - 8}" text-anchor="middle" class="tick">'
            f"{html.escape(label)}</text>"
        )
    for _label, series_values, color, dash in series:
        if not series_values:
            continue
        points = " ".join(
            f"{left + step_x * index:.1f},{top + plot_h * (1.0 - (value - low) / span):.1f}"
            for index, value in enumerate(series_values)
        )
        dash_attr = f" {dash}" if dash else ""
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="{color}" '
            f'stroke-width="1.7" stroke-linejoin="round"{dash_attr}/>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _day_ticks(days: int) -> list[tuple[int, str]]:
    """Tick positions for a daily series, thinned to about seven labels."""
    step = max(1, days // 7)
    return [(day, str(day + 1)) for day in range(step - 1, days, step)]


def _hour_ticks(days: int) -> list[tuple[int, str]]:
    """Day boundaries of an hourly series, labelled with the day number.

    The chart indexes points, not days, so a daily tick has to be converted to
    its hour index; passing day numbers straight in would pile every label into
    the first 28 pixels.
    """
    return [(day * HOURS_PER_DAY, label) for day, label in _day_ticks(days)]


def _color(key: str) -> str:
    if key in CROP_COLORS:
        return CROP_COLORS[key]
    return FALLBACK_COLORS[sum(ord(char) for char in key) % len(FALLBACK_COLORS)]


def _dash_style(dash: str | None) -> str:
    return "border-top-style:dashed" if dash else "border-top-style:solid"


def _sign(value: float) -> str:
    if value > 1e-9:
        return "pos"
    if value < -1e-9:
        return "neg"
    return ""


def summarize(result: ProbeResult) -> list[dict[str, Any]]:
    """Flat rows for the CLI table."""
    rows: list[dict[str, Any]] = []
    for regime in result.regimes:
        for item in regime.comparisons:
            rows.append(
                {
                    "regime": regime.tariff.key,
                    "crop": item.label,
                    "solo_unit": item.solo_unit_cost,
                    "pooled_unit": item.pooled_unit_cost,
                    "delta_unit": item.delta_unit,
                    "delta_yield": item.delta_yield,
                    "delta_margin": item.delta_margin,
                    "pressure": item.pressure,
                }
            )
    return rows
