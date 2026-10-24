"""Tests for the crop templates and the tables derived from them.

Two things are worth defending here.  First, the extraction must not have moved
a single number: the anchors below are the literals the templates replaced, and
they are compared exactly, not approximately.  Second, a hand-edited file must
fail loudly — every rule in ``CropTemplate`` has a test that violates exactly
that rule and nothing else.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from rancho.agronomy import CROP_WATER
from rancho.crops import (
    STAGE_ORDER,
    TEMPLATES,
    CropTemplateError,
    load_template,
    load_templates,
)
from rancho.econ.spec import CROPS
from rancho.engine import PLUGINS
from rancho.scenario import CROP_KEYS, CROP_PROFILES, STAGE_LABELS
from rancho.vegetables import CUCUMBER, LETTUCE, TOMATO, VegetableProfile
from rancho.vegetables import PLUGINS as VEGETABLE_PLUGINS

#: The values the templates must reproduce — copied from the code they replaced
#: (``vegetables.py``, ``agronomy.CROP_WATER``, ``scenario.CROP_PROFILES``,
#: ``econ/spec.py``).  ``growth`` / ``structure`` / ``market`` are ``None`` for a
#: crop whose phenology CropForge owns.
ANCHORS: dict[str, dict[str, object]] = {
    "tomato": {
        "label": "番茄",
        "species": "Solanum lycopersicum",
        "base_temp_c": 10.0,
        "stages": (
            ("germination", 0.0),
            ("emergence", 55.0),
            ("seedling", 110.0),
            ("vegetative", 300.0),
            ("flowering", 500.0),
            ("fruit_set", 600.0),
            ("fruiting", 700.0),
            ("maturity", 1900.0),
        ),
        "growth": (1.55, 0.0045, 3.4, 0.012, ("maturity",), 0.45),
        "structure": (8.0, 220.0),
        "water": (450.0, 1.15, 0.40, 0.65, 0.032, (0.03, 0.18, 0.34, 0.50, 0.66, 0.80)),
        "product": (0.055, 0.92),
        "market": (14.0, 20.0, 130.0, 26.0, 18.0, 22.0, 9.0, 32.0, 34.0, 0.020, 85.0, 11.0, 0.35),
    },
    "cucumber": {
        "label": "黄瓜",
        "species": "Cucumis sativus",
        "base_temp_c": 12.0,
        "stages": (
            ("germination", 0.0),
            ("emergence", 50.0),
            ("seedling", 100.0),
            ("vegetative", 220.0),
            ("flowering", 330.0),
            ("fruit_set", 380.0),
            ("fruiting", 430.0),
            ("maturity", 900.0),
        ),
        "growth": (1.45, 0.0070, 3.8, 0.015, ("maturity",), 0.55),
        "structure": (9.0, 250.0),
        "water": (350.0, 1.05, 0.45, 0.70, 0.030, (0.03, 0.20, 0.40, 0.60)),
        "product": (0.045, 0.95),
        "market": (9.0, 18.0, 110.0, 26.0, 17.0, 24.0, 12.0, 34.0, 35.0, 0.018, 100.0, 10.0, 0.32),
    },
    "lettuce": {
        "label": "生菜",
        "species": "Lactuca sativa",
        "base_temp_c": 4.0,
        "stages": (
            ("germination", 0.0),
            ("seedling", 60.0),
            ("vegetative", 180.0),
            ("maturity", 520.0),
        ),
        "growth": (0.80, 0.0035, 4.5, 0.020, (), 0.75),
        "structure": (3.2, 30.0),
        "water": (200.0, 1.00, 0.35, 0.70, 0.035, (0.05, 0.30, 0.55)),
        "product": (0.050, 0.90),
        "market": (7.0, 12.0, 90.0, 22.0, 16.0, 18.0, 4.0, 27.0, 29.0, 0.025, 120.0, 8.0, 0.25),
    },
    "wheat": {
        "label": "小麦",
        "species": "Triticum aestivum",
        "base_temp_c": None,
        "stages": (),
        "growth": None,
        "structure": None,
        "water": (300.0, 1.05, 0.50, 0.45, 0.028, (0.02, 0.22, 0.40)),
        "product": (1.0, 1.0),
        "market": None,
    },
    "maize": {
        "label": "玉米",
        "species": "Zea mays",
        "base_temp_c": None,
        "stages": (),
        "growth": None,
        "structure": None,
        "water": (400.0, 1.15, 0.55, 0.55, 0.025, (0.02, 0.22, 0.40)),
        "product": (1.0, 1.0),
        "market": None,
    },
}

#: Smallest template that validates: one crop, two stages, one of everything.
BASE = """
id = "basil"
label = "罗勒"
species = "Ocimum basilicum"
base_temp_c = 8.0

[[stages]]
name = "germination"
gdd = 0.0

[[stages]]
name = "maturity"
gdd = 100.0

[growth]
rue = 1.0
lai_slope = 0.001
lai_max = 2.0
senescence_rate = 0.010

[structure]
k_height = 4.0
max_height_cm = 40.0

[water]
root_zone_mm = 150.0
kc_mid = 1.0
depletion_fraction = 0.40
k_extinction = 0.60
n_concentration = 0.030
splits = [0.10, 0.50]

[product]
dry_matter_fraction = 0.060
marketable_fraction = 0.90
"""

#: The economic block, appended to ``BASE`` when a test needs a priced crop.
MARKET = """
[market]
price_yuan_kg = 12.0
dli_target = 14.0
light_w_m2 = 100.0
setpoint_day_c = 24.0
setpoint_night_c = 17.0
t_opt_c = 21.0
t_min_c = 8.0
t_max_c = 30.0
t_critical_c = 33.0
damage_per_hour_k = 0.020
growth_max_g_m2_day = 60.0
dli_half = 9.0
fert_g_m2_day = 0.30
"""


def _write(tmp_path: Path, text: str, name: str = "basil.toml") -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def _rejects(path: Path, fragment: str) -> str:
    """Assert the file is refused with *fragment* in the message; return it."""
    with pytest.raises(CropTemplateError) as raised:
        load_template(path)
    message = str(raised.value)
    assert fragment in message, message
    return message


def _growth(template) -> tuple | None:
    if template.growth is None:
        return None
    growth = template.growth
    return (
        growth.rue,
        growth.lai_slope,
        growth.lai_max,
        growth.senescence_rate,
        growth.senescence_stages,
        growth.transplant_lai,
    )


def _structure(template) -> tuple | None:
    if template.structure is None:
        return None
    return (template.structure.k_height, template.structure.max_height_cm)


def _market(template) -> tuple | None:
    if template.market is None:
        return None
    market = template.market
    return (
        market.price_yuan_kg,
        market.dli_target,
        market.light_w_m2,
        market.setpoint_day_c,
        market.setpoint_night_c,
        market.t_opt_c,
        market.t_min_c,
        market.t_max_c,
        market.t_critical_c,
        market.damage_per_hour_k,
        market.growth_max_g_m2_day,
        market.dli_half,
        market.fert_g_m2_day,
    )


def _water(template) -> tuple:
    water = template.water
    return (
        water.root_zone_mm,
        water.kc_mid,
        water.depletion_fraction,
        water.k_extinction,
        water.n_concentration,
        water.splits,
    )


# ------------------------------------------------------------- the bundled set


def test_the_bundled_set_is_three_vegetables_and_two_cereals() -> None:
    assert set(TEMPLATES) == set(ANCHORS)
    assert {key for key, crop in TEMPLATES.items() if crop.has_phenology} == {
        "tomato",
        "cucumber",
        "lettuce",
    }
    assert {key for key, crop in TEMPLATES.items() if not crop.has_phenology} == {
        "wheat",
        "maize",
    }


def test_every_templated_crop_is_selectable_in_a_scenario() -> None:
    """A template the config cannot name would be invisible to the engine."""
    assert set(TEMPLATES) <= CROP_KEYS


def test_an_unknown_crop_is_refused_by_the_scenario() -> None:
    from rancho.scenario import ScenarioConfig

    with pytest.raises(Exception, match="未知作物"):
        ScenarioConfig(crop="basil")


@pytest.mark.parametrize("key", sorted(ANCHORS))
def test_the_template_reproduces_the_literal_it_replaced(key: str) -> None:
    template = TEMPLATES[key]
    anchor = ANCHORS[key]
    assert template.label == anchor["label"]
    assert template.species == anchor["species"]
    assert template.base_temp_c == anchor["base_temp_c"]
    assert tuple((stage.name, stage.gdd) for stage in template.stages) == anchor["stages"]
    assert _growth(template) == anchor["growth"]
    assert _structure(template) == anchor["structure"]
    assert _water(template) == anchor["water"]
    assert (
        template.product.dry_matter_fraction,
        template.product.marketable_fraction,
    ) == anchor["product"]
    assert _market(template) == anchor["market"]


@pytest.mark.parametrize(
    ("key", "profile"),
    [("tomato", TOMATO), ("cucumber", CUCUMBER), ("lettuce", LETTUCE)],
)
def test_the_growth_plugin_reads_the_template(key: str, profile: VegetableProfile) -> None:
    template = TEMPLATES[key]
    growth = template.growth
    structure = template.structure
    base_temp_c = template.base_temp_c
    assert growth is not None and structure is not None and base_temp_c is not None
    assert profile == VegetableProfile(
        species=template.species,
        base_temp_c=base_temp_c,
        stages=tuple((stage.name, stage.gdd) for stage in template.stages),
        rue=growth.rue,
        lai_slope=growth.lai_slope,
        lai_max=growth.lai_max,
        senescence_rate=growth.senescence_rate,
        senescence_stages=growth.senescence_stages,
        k_height=structure.k_height,
        max_height_cm=structure.max_height_cm,
    )


@pytest.mark.parametrize("key", sorted(ANCHORS))
def test_the_water_and_product_tables_read_the_same_file(key: str) -> None:
    template = TEMPLATES[key]
    water = CROP_WATER[key]
    flat = (
        water.root_zone_mm,
        water.kc_mid,
        water.depletion_fraction,
        water.k_extinction,
        water.n_concentration,
        water.splits,
    )
    assert flat == _water(template)
    product = CROP_PROFILES[key]
    assert product.label == template.label
    assert product.species == template.species
    assert product.dry_matter_fraction == template.product.dry_matter_fraction
    assert product.marketable_fraction == template.product.marketable_fraction


def test_the_cereals_keep_only_the_coefficients_rancho_owns() -> None:
    """No phenology, no market: CropForge grows them, nobody auctions them."""
    assert set(CROP_WATER) == set(TEMPLATES)
    for key in ("wheat", "maize"):
        assert not TEMPLATES[key].has_phenology
        assert TEMPLATES[key].market is None
        assert key not in VEGETABLE_PLUGINS


def test_every_crop_has_a_plugin_somewhere() -> None:
    """A template with no plugin to run it would fail halfway through a run."""
    assert set(TEMPLATES) <= set(PLUGINS)
    assert set(VEGETABLE_PLUGINS) == {"tomato", "cucumber", "lettuce"}


def test_the_economic_layer_prices_crops_from_their_market_block() -> None:
    for key, spec in CROPS.items():
        template = TEMPLATES[key]
        market = template.market
        growth = template.growth
        assert market is not None and growth is not None
        assert spec.label == template.label
        assert spec.price_yuan_kg == market.price_yuan_kg
        assert spec.t_opt_c == market.t_opt_c
        assert spec.t_critical_c == market.t_critical_c
        assert spec.growth_max_g_m2_day == market.growth_max_g_m2_day
        assert spec.dli_half == market.dli_half
        assert spec.fert_g_m2_day == market.fert_g_m2_day
        assert spec.transplant_lai == growth.transplant_lai


def test_a_crop_without_a_market_block_cannot_be_priced() -> None:
    """The error has to name the crop, not surface as a bare ``None`` later."""
    from rancho.econ.spec import _crop

    with pytest.raises(ValueError, match=r"wheat 模板缺少 \[market\]"):
        _crop(
            "wheat",
            area_m2=100.0,
            initial_n_kg_ha=20.0,
            initial_moisture_pct=20.0,
            field_capacity_pct=32.0,
            wilting_point_pct=14.0,
        )


def test_every_stage_in_the_vocabulary_has_a_label() -> None:
    """A stage added to one table only would render as a raw key in the UI."""
    assert set(STAGE_ORDER) == set(STAGE_LABELS)


# ------------------------------------------------------------------ validation


def test_a_minimal_template_loads(tmp_path: Path) -> None:
    template = load_template(_write(tmp_path, BASE))
    growth = template.growth
    assert growth is not None
    assert template.season_gdd == 100.0
    assert growth.senescence_stages == ()
    assert template.water.splits == (0.10, 0.50)
    assert template.market is None


def test_a_template_may_carry_a_market_block(tmp_path: Path) -> None:
    template = load_template(_write(tmp_path, f"{BASE}{MARKET}"))
    assert template.market is not None
    assert template.market.price_yuan_kg == 12.0


def test_a_hand_written_integer_is_accepted_as_a_float(tmp_path: Path) -> None:
    """Manual entry will produce ``rue = 2`` sooner or later; that is fine."""
    template = load_template(_write(tmp_path, BASE.replace("rue = 1.0", "rue = 2")))
    growth = template.growth
    assert growth is not None
    assert growth.rue == 2.0
    assert isinstance(growth.rue, float)


def test_a_directory_of_templates_is_keyed_by_file_stem(tmp_path: Path) -> None:
    second = BASE.replace('id = "basil"', 'id = "mint"').replace("罗勒", "薄荷")
    _write(tmp_path, BASE)
    _write(tmp_path, second, name="mint.toml")
    loaded = load_templates(tmp_path)
    assert set(loaded) == {"basil", "mint"}
    assert loaded["mint"].label == "薄荷"


def test_an_empty_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CropTemplateError, match="没有任何作物模板"):
        load_templates(tmp_path)


def test_a_stage_that_does_not_advance_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, BASE.replace("gdd = 100.0", "gdd = 0.0"))
    message = _rejects(path, "必须严格递增")
    assert "maturity" in message


def test_the_first_stage_must_start_at_zero(tmp_path: Path) -> None:
    path = _write(tmp_path, BASE.replace("gdd = 0.0", "gdd = 5.0"))
    _rejects(path, "gdd 必须为 0.0")


def test_an_unknown_stage_name_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, BASE.replace('name = "maturity"', 'name = "harvest"'))
    message = _rejects(path, "未知物候阶段")
    assert "harvest" in message


def test_a_duplicated_stage_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, BASE.replace('name = "maturity"', 'name = "germination"'))
    _rejects(path, "物候阶段重复")


def test_a_senescence_stage_must_be_one_of_the_stages(tmp_path: Path) -> None:
    text = BASE.replace(
        "senescence_rate = 0.010", 'senescence_rate = 0.010\nsenescence_stages = ["harvest"]'
    )
    path = _write(tmp_path, text)
    _rejects(path, "senescence_stages 引用了未定义的阶段")


def test_a_partial_phenology_block_is_refused(tmp_path: Path) -> None:
    """[growth] without [structure] leaves the canopy with no height to cap."""
    text = textwrap.dedent(BASE)
    stripped = text[: text.index("[structure]")] + text[text.index("[water]") :]
    path = _write(tmp_path, stripped)
    assert "[structure]" not in path.read_text(encoding="utf-8")
    _rejects(path, "物候参数必须整组给出")


def test_fertiliser_splits_must_advance_through_the_season(tmp_path: Path) -> None:
    path = _write(tmp_path, BASE.replace("splits = [0.10, 0.50]", "splits = [0.50, 0.10]"))
    _rejects(path, "splits 必须严格递增")


def test_a_split_outside_the_season_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, BASE.replace("splits = [0.10, 0.50]", "splits = [0.0, 0.50]"))
    _rejects(path, "splits 必须落在 (0, 1]")


def test_the_temperature_limits_must_be_ordered(tmp_path: Path) -> None:
    path = _write(tmp_path, f"{BASE}{MARKET}".replace("t_opt_c = 21.0", "t_opt_c = 31.0"))
    _rejects(path, "t_min < t_opt < t_max")


def test_a_night_setpoint_above_the_day_one_is_refused(tmp_path: Path) -> None:
    text = f"{BASE}{MARKET}".replace("setpoint_night_c = 17.0", "setpoint_night_c = 27.0")
    _rejects(_write(tmp_path, text), "夜间设定点")


def test_an_unknown_field_is_refused_rather_than_ignored(tmp_path: Path) -> None:
    path = _write(tmp_path, BASE.replace("base_temp_c = 8.0", 'base_temp_c = 8.0\ncolour = "red"'))
    _rejects(path, "colour")


def test_a_field_out_of_range_names_the_section(tmp_path: Path) -> None:
    path = _write(tmp_path, BASE.replace("depletion_fraction = 0.40", "depletion_fraction = 1.20"))
    message = _rejects(path, "water.depletion_fraction")
    assert "basil.toml" in message


def test_the_file_name_must_match_the_crop_id(tmp_path: Path) -> None:
    path = _write(tmp_path, BASE, name="not_basil.toml")
    _rejects(path, "必须与文件名一致")


def test_a_syntax_error_names_the_file(tmp_path: Path) -> None:
    path = _write(tmp_path, f"{BASE}\n[broken\n")
    message = _rejects(path, "TOML 语法错误")
    assert "basil.toml" in message


def test_a_template_without_stages_is_refused(tmp_path: Path) -> None:
    both = (
        '[[stages]]\nname = "germination"\ngdd = 0.0\n\n'
        '[[stages]]\nname = "maturity"\ngdd = 100.0\n'
    )
    path = _write(tmp_path, textwrap.dedent(BASE).replace(both, ""))
    _rejects(path, "stages")


def test_loading_the_bundled_directory_twice_gives_the_same_crops() -> None:
    """The loader keeps no hidden state, so re-reading a hand edit is enough."""
    assert load_templates() == TEMPLATES
