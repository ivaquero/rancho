"""Per-cell crop selection — the parts that must not silently converge.

Three things are defended here:

* the validation that keeps a layout honest (right length, mixable crops only);
* the dispatcher, which has to hand every plant to *its own* crop and nothing
  else — a uniform map must behave exactly like registering the plugin directly;
* the water and nitrogen balance, which reads root zone, ``Kc`` and the
  fertiliser calendar per cell rather than per field.

The last one is the reason this file exists at all: a mixed field can otherwise
run without error while pretending every bed is a tomato.
"""

from __future__ import annotations

import numpy as np
import pytest
from cropforge.state import EnvironmentState, PlantState

from rancho.agronomy import AgronomyModel
from rancho.climate import SoilField, build_soil_field
from rancho.mosaic import CropMosaic
from rancho.scenario import (
    MOSAIC_KEYS,
    ScenarioConfig,
    crop_grid,
    crop_indices,
    crop_keys,
    unique_in_order,
)
from rancho.vegetables import PLUGINS as VEGETABLE_PLUGINS

#: The smallest field the scenario accepts.
ROWS = 4
COLS = 4

#: tomato / lettuce checkerboard, row-major — the layout every case below uses.
MIXED: list[str] = [
    ("tomato" if (row + col) % 2 == 0 else "lettuce") for row in range(ROWS) for col in range(COLS)
]


def _plants(rows: int = ROWS, cols: int = COLS) -> list[PlantState]:
    return [
        PlantState(plant_id=f"r{r:02d}c{c:02d}", row=r, col=c)
        for r in range(rows)
        for c in range(cols)
    ]


def _env(temp: float = 20.0, **overrides: float) -> EnvironmentState:
    return EnvironmentState(
        day=1,
        doy=1,
        temp_max_c=temp + 5.0,
        temp_min_c=temp - 5.0,
        temp_mean_c=temp,
        radiation_mj_m2=12.0,
        rainfall_mm=overrides.get("rainfall_mm", 0.0),
        et0_mm=overrides.get("et0_mm", 4.0),
        wind_speed_ms=1.0,
        humidity_pct=70.0,
    )


class Voxel:
    """The two fields the write-back touches."""

    moisture_pct = 0.0
    nitrogen_kg_ha = 0.0


class _State:
    """Just enough of ``FieldState`` for the balance and the plugins."""

    def __init__(self, plants: list[PlantState], rows: int = ROWS, cols: int = COLS) -> None:
        self.plants = plants
        self.soil = [
            [[Voxel() for _layer in range(3)] for _col in range(cols)] for _row in range(rows)
        ]


# --------------------------------------------------------------- the layout


def test_scenario_without_a_map_is_one_crop_everywhere() -> None:
    cfg = ScenarioConfig(crop="tomato", rows=4, cols=5)
    assert crop_grid(cfg) == ["tomato"] * 20
    assert crop_keys(cfg) == ("tomato",)
    assert crop_indices(cfg) == [0] * 20


def test_a_layout_is_read_in_first_appearance_order() -> None:
    cfg = ScenarioConfig(crop="tomato", rows=ROWS, cols=COLS, crop_map=MIXED)
    assert crop_keys(cfg) == ("tomato", "lettuce")
    assert crop_indices(cfg) == [int(index) for index in "0101101001011010"]


def test_the_dispatcher_and_the_scenario_agree_on_the_order() -> None:
    """Three consumers index into the same tuple; they must build it the same way.

    :meth:`CropMosaic.keys` picks the sub-plugin per cell, ``crop_keys`` gives
    the client the index it colours cells with, and the agronomy model resolves
    its per-crop coefficients through it.  A different order in any one of them
    grows or colours the wrong bed.
    """
    cfg = ScenarioConfig(crop="tomato", rows=ROWS, cols=COLS, crop_map=MIXED)
    dispatcher = CropMosaic(crop_map=crop_grid(cfg), rows=ROWS, cols=COLS)
    assert dispatcher.keys == crop_keys(cfg)
    # And the plain helper both of them use, on its own.
    assert unique_in_order(["lettuce", "tomato", "lettuce"]) == ("lettuce", "tomato")


def test_a_map_that_does_not_cover_the_field_is_refused() -> None:
    with pytest.raises(Exception, match="crop_map 有 15 项"):
        ScenarioConfig(crop="tomato", rows=ROWS, cols=COLS, crop_map=["tomato"] * 15)


def test_a_crop_cropforge_models_cannot_be_placed_per_cell() -> None:
    """No per-plant form exists for their steppers, so the config must say so."""
    with pytest.raises(Exception, match="逐格混作只支持"):
        ScenarioConfig(crop="tomato", rows=ROWS, cols=COLS, crop_map=["wheat"] * 16)
    assert "wheat" not in MOSAIC_KEYS
    assert set(MOSAIC_KEYS) == set(VEGETABLE_PLUGINS)


def test_resizing_the_grid_carries_the_layout_across() -> None:
    cfg = ScenarioConfig(crop="tomato", rows=ROWS, cols=COLS, crop_map=MIXED)
    bigger = cfg.with_grid(8, 8)
    assert (bigger.rows, bigger.cols) == (8, 8)
    assert len(bigger.crop_map or []) == 64
    # Every crop survives the change, or resizing would be a reset in disguise.
    assert set(bigger.crop_map or []) == {"tomato", "lettuce"}
    # Nearest relative position: the corners keep what the old corners held.
    assert (bigger.crop_map or [])[0] == "tomato"
    assert (bigger.crop_map or [])[7] == "lettuce"


# ------------------------------------------------------------ the dispatcher


def test_each_plant_advances_on_its_own_crops_climate() -> None:
    """Thermal time is the cheapest observable that proves the dispatch."""
    mosaic = CropMosaic(crop_map=MIXED, rows=ROWS, cols=COLS)
    state = _State(_plants())
    mosaic.step(state, _env(temp=20.0))

    thermal = [round(plant.custom["thermal_time"], 3) for plant in state.plants]
    # tomato counts degree-days above 10 °C, lettuce above 4 °C: same weather,
    # different sums — so a plant that got the wrong plugin shows up here.
    expected = [10.0 if key == "tomato" else 16.0 for key in MIXED]
    assert thermal == expected


def test_a_uniform_map_is_indistinguishable_from_the_plugin_itself() -> None:
    """Otherwise every existing single-crop run would silently change."""
    uniform = CropMosaic(crop_map=["tomato"] * 16, rows=ROWS, cols=COLS)
    dispatched = _State(_plants())
    uniform.step(dispatched, _env())

    direct = _State(_plants())
    VEGETABLE_PLUGINS["tomato"]().step(direct, _env())

    for left, right in zip(dispatched.plants, direct.plants, strict=True):
        assert left.custom == right.custom
        assert left.lai == right.lai
        assert left.biomass_g == right.biomass_g
        assert left.height_cm == right.height_cm
        assert left.phenological_stage == right.phenological_stage


def test_a_mismatched_map_is_refused_before_any_plant_moves() -> None:
    with pytest.raises(ValueError, match="crop_map 有 5 项"):
        CropMosaic(crop_map=["tomato"] * 5, rows=ROWS, cols=COLS)


# ------------------------------------------------------------ the balance


def _mixed_config(**overrides: object) -> ScenarioConfig:
    """A mixed tomato / lettuce field, flat and rainfed, with nothing fed to it."""
    cfg = ScenarioConfig(
        crop="tomato",
        rows=ROWS,
        cols=COLS,
        crop_map=MIXED,
        irrigation=False,
        soil_pattern="uniform",
        soil_variation=0.0,
        fertiliser_kg_ha=0.0,
    )
    return cfg.model_copy(update=dict(overrides)) if overrides else cfg


def _soil_field(cfg: ScenarioConfig) -> SoilField:
    return build_soil_field(
        cfg.soil,
        rows=cfg.rows,
        cols=cfg.cols,
        pattern="uniform",
        variation=0.0,
        seed=7,
        elevation=np.zeros((cfg.rows, cfg.cols), dtype=np.float64),
    )


def test_the_water_balance_reads_its_coefficients_per_cell() -> None:
    cfg = _mixed_config()
    model = AgronomyModel(cfg, _soil_field(cfg))
    state = _State(_plants())
    model.step(state, _env(et0_mm=5.0))

    moisture = {
        key: [
            state.soil[row][col][0].moisture_pct
            for row in range(ROWS)
            for col in range(COLS)
            if MIXED[row * COLS + col] == key
        ]
        for key in ("tomato", "lettuce")
    }

    # Same day, same weather: the tomato's 450 mm root zone behind a higher Kc
    # still loses fewer percentage points than lettuce's 200 mm one.
    assert min(moisture["tomato"]) > max(moisture["lettuce"])
    # ...and the two halves differ at all, which is the point of resolving
    # coefficients per cell instead of once per field.
    assert not np.isclose(min(moisture["tomato"]), max(moisture["lettuce"]))


def test_each_crop_is_fed_on_its_own_schedule() -> None:
    """Six splits versus three: both beds still receive the season total."""
    cfg = _mixed_config(days=100, fertiliser_kg_ha=120.0)
    model = AgronomyModel(cfg, _soil_field(cfg))
    state = _State(_plants())

    fed: dict[str, list[int]] = {"tomato": [], "lettuce": []}
    for day in range(1, 101):
        # Nitrogen is the observable: the beds are fed on different days.
        before = model._nitrogen.copy()
        env = _env()
        env.day = day
        model.step(state, env)
        after = model._nitrogen
        for key, cell in (("tomato", (0, 0)), ("lettuce", (0, 1))):
            row, col = cell
            if float(after[row, col]) - float(before[row, col]) > 1.0:
                fed[key].append(day)

    assert fed["tomato"] == [3, 18, 34, 50, 66, 80]
    assert fed["lettuce"] == [5, 30, 55]


# --------------------------------------------------------------- end to end


def test_a_mixed_field_runs_and_reports_one_ledger_row_per_crop(tmp_path) -> None:
    from rancho.engine import run_scenario

    cfg = ScenarioConfig(
        name="Mezcla",
        rows=4,
        cols=6,
        days=12,
        crop="tomato",
        crop_map=["tomato", "tomato", "tomato", "lettuce", "lettuce", "lettuce"] * 4,
        climate="tropical",
        soil="andisol",
        protected=True,
        cover_transmission=0.55,
        irrigation=False,
        latitude=13.69,
        longitude=-89.22,
    )
    bundle = run_scenario(cfg, tmp_path)

    assert bundle["meta"]["mosaic"] is True
    assert [row["key"] for row in bundle["meta"]["crops"]] == ["tomato", "lettuce"]
    assert bundle["grid"]["crop_index"] == crop_indices(cfg)

    ledger = bundle["yield"]["crops"]
    assert [row["key"] for row in ledger] == ["tomato", "lettuce"]
    assert all(row["cells"] == 12 for row in ledger)
    # The area-weighted total is what a hectare of this mix would produce, so it
    # has to sit between the two single-crop figures.
    assert min(row["t_per_ha"] for row in ledger) <= bundle["yield"]["t_per_ha"]
    assert max(row["t_per_ha"] for row in ledger) >= bundle["yield"]["t_per_ha"]
    assert bundle["yield"]["total_kg"] == pytest.approx(
        sum(row["total_kg"] for row in ledger), abs=0.2
    )
