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
from typing import get_args

import pytest

from rancho.agronomy import CROP_WATER
from rancho.crops import (
    STAGE_ORDER,
    TEMPLATES,
    CropTemplate,
    CropTemplateError,
    load_template,
    load_templates,
)
from rancho.scenario import CROP_PROFILES, STAGE_LABELS, CropKind
from rancho.vegetables import CUCUMBER, LETTUCE, TOMATO, VegetableProfile

#: The values the templates must reproduce — copied from the code they replaced
#: (``vegetables.py``, ``agronomy.CROP_WATER``, ``scenario.CROP_PROFILES``).
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
        "growth": (1.55, 0.0045, 3.4, 0.012, ("maturity",)),
        "structure": (8.0, 220.0),
        "water": (450.0, 1.15, 0.40, 0.65, 0.032, (0.03, 0.18, 0.34, 0.50, 0.66, 0.80)),
        "product": (0.055, 0.92),
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
        "growth": (1.45, 0.0070, 3.8, 0.015, ("maturity",)),
        "structure": (9.0, 250.0),
        "water": (350.0, 1.05, 0.45, 0.70, 0.030, (0.03, 0.20, 0.40, 0.60)),
        "product": (0.045, 0.95),
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
        "growth": (0.80, 0.0035, 4.5, 0.020, ()),
        "structure": (3.2, 30.0),
        "water": (200.0, 1.00, 0.35, 0.70, 0.035, (0.05, 0.30, 0.55)),
        "product": (0.050, 0.90),
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


# ------------------------------------------------------------- the bundled set


def test_the_bundled_set_is_exactly_the_three_vegetables() -> None:
    assert set(TEMPLATES) == set(ANCHORS) == {"tomato", "cucumber", "lettuce"}


def test_every_templated_crop_is_selectable_in_a_scenario() -> None:
    """A template the config cannot name would be invisible to the engine."""
    assert set(TEMPLATES) <= set(get_args(CropKind))


@pytest.mark.parametrize("key", sorted(ANCHORS))
def test_the_template_reproduces_the_literal_it_replaced(key: str) -> None:
    template = TEMPLATES[key]
    anchor = ANCHORS[key]
    assert template.label == anchor["label"]
    assert template.species == anchor["species"]
    assert template.base_temp_c == anchor["base_temp_c"]
    assert tuple((stage.name, stage.gdd) for stage in template.stages) == anchor["stages"]
    assert (
        template.growth.rue,
        template.growth.lai_slope,
        template.growth.lai_max,
        template.growth.senescence_rate,
        template.growth.senescence_stages,
    ) == anchor["growth"]
    assert (template.structure.k_height, template.structure.max_height_cm) == anchor["structure"]
    assert (
        template.water.root_zone_mm,
        template.water.kc_mid,
        template.water.depletion_fraction,
        template.water.k_extinction,
        template.water.n_concentration,
        template.water.splits,
    ) == anchor["water"]
    assert (
        template.product.dry_matter_fraction,
        template.product.marketable_fraction,
    ) == anchor["product"]


@pytest.mark.parametrize(
    ("key", "profile"),
    [("tomato", TOMATO), ("cucumber", CUCUMBER), ("lettuce", LETTUCE)],
)
def test_the_growth_plugin_reads_the_template(key: str, profile: VegetableProfile) -> None:
    template = TEMPLATES[key]
    assert profile == VegetableProfile(
        species=template.species,
        base_temp_c=template.base_temp_c,
        stages=tuple((stage.name, stage.gdd) for stage in template.stages),
        rue=template.growth.rue,
        lai_slope=template.growth.lai_slope,
        lai_max=template.growth.lai_max,
        senescence_rate=template.growth.senescence_rate,
        senescence_stages=template.growth.senescence_stages,
        k_height=template.structure.k_height,
        max_height_cm=template.structure.max_height_cm,
    )


@pytest.mark.parametrize("key", sorted(ANCHORS))
def test_the_water_and_product_tables_read_the_same_file(key: str) -> None:
    template = TEMPLATES[key]
    water = CROP_WATER[key]
    product = CROP_PROFILES[key]
    assert water.root_zone_mm == template.water.root_zone_mm
    assert water.kc_mid == template.water.kc_mid
    assert water.splits == template.water.splits
    assert product.label == template.label
    assert product.species == template.species
    assert product.dry_matter_fraction == template.product.dry_matter_fraction
    assert product.marketable_fraction == template.product.marketable_fraction


def test_the_cereals_are_still_not_templated() -> None:
    """CropForge owns the cereal plugins; their numbers stay in Rancho's tables."""
    assert set(CROP_WATER) == {"wheat", "maize", *TEMPLATES}
    assert CROP_WATER["wheat"].root_zone_mm == 300.0
    assert CROP_WATER["maize"].kc_mid == 1.15
    assert CROP_PROFILES["wheat"].label == "小麦"


def test_every_stage_in_the_vocabulary_has_a_label() -> None:
    """A stage added to one table only would render as a raw key in the UI."""
    assert set(STAGE_ORDER) == set(STAGE_LABELS)


# ------------------------------------------------------------------ validation


def test_a_minimal_template_loads(tmp_path: Path) -> None:
    template = load_template(_write(tmp_path, BASE))
    assert template.season_gdd == 100.0
    assert template.growth.senescence_stages == ()
    assert template.water.splits == (0.10, 0.50)


def test_a_hand_written_integer_is_accepted_as_a_float(tmp_path: Path) -> None:
    """Manual entry will produce ``rue = 2`` sooner or later; that is fine."""
    template = load_template(_write(tmp_path, BASE.replace("rue = 1.0", "rue = 2")))
    assert template.growth.rue == 2.0
    assert isinstance(template.growth.rue, float)


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


def test_fertiliser_splits_must_advance_through_the_season(tmp_path: Path) -> None:
    path = _write(tmp_path, BASE.replace("splits = [0.10, 0.50]", "splits = [0.50, 0.10]"))
    _rejects(path, "splits 必须严格递增")


def test_a_split_outside_the_season_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path, BASE.replace("splits = [0.10, 0.50]", "splits = [0.0, 0.50]"))
    _rejects(path, "splits 必须落在 (0, 1]")


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


def test_the_template_is_immutable_where_the_engine_reads_it() -> None:
    """The plugin holds a frozen view; mutating a template must not be possible."""
    with pytest.raises(Exception, match="frozen"):
        TEMPLATES["tomato"].growth.rue = 9.0  # ty: ignore[invalid-assignment]
    assert isinstance(TEMPLATES["tomato"], CropTemplate)
