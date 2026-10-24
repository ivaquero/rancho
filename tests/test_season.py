"""The horizon follows the crop — the parts that must not drift apart.

A scenario may name how long it runs, and two of the presets do on purpose
(a drought trial is not a full season).  Every other one stays silent and lets
the field decide: the run lasts until the *slowest* crop in it has matured, so
lettuce standing next to tomato does not cut the tomato short.

Defended here: which crop sets the horizon, that a named horizon wins over it,
and that both spellings of the same run are the same run — otherwise the cache
fills with near-duplicates of one field.
"""

from __future__ import annotations

from rancho.crops import TEMPLATES
from rancho.engine import run_id_for
from rancho.scenario import PRESETS, ScenarioConfig, horizon_days, season_days, with_horizon

#: The three greenhouse crops, longest season first.
TOMATO = TEMPLATES["tomato"].season.days_to_maturity
CUCUMBER = TEMPLATES["cucumber"].season.days_to_maturity
LETTUCE = TEMPLATES["lettuce"].season.days_to_maturity


def _field(
    *, crop: str, crop_map: list[str] | None = None, rows: int = 4, cols: int = 4
) -> ScenarioConfig:
    return ScenarioConfig(
        name="Test Plot",
        rows=rows,
        cols=cols,
        crop=crop,
        crop_map=crop_map,
        climate="tropical",
        soil="andisol",
    )


def test_the_slowest_crop_sets_the_horizon() -> None:
    """Tomato ripens at 130 days; lettuce beside it must not end the run at 60."""
    assert LETTUCE < TOMATO  # the point of the case: not a tie
    alone = _field(crop="lettuce")
    mixed = _field(crop="tomato", crop_map=["lettuce"] * 8 + ["tomato"] * 8)
    assert season_days(alone) == LETTUCE
    assert season_days(mixed) == TOMATO


def test_a_cereal_sets_it_too_although_cropforge_owns_its_phenology() -> None:
    """Wheat has no [[stages]], but how long it stands is still its own number."""
    assert TEMPLATES["wheat"].season_gdd == 0.0
    assert season_days(_field(crop="wheat")) == TEMPLATES["wheat"].season.days_to_maturity


def test_a_named_horizon_wins_over_the_crop() -> None:
    """Cutting a season short is a decision, not a bug — say it and it happens."""
    cfg = _field(crop="tomato").model_copy(update={"days": 40})
    assert horizon_days(cfg) == 40
    assert season_days(cfg) == TOMATO  # the crop still *asks* for 130


def test_with_horizon_fills_the_number_in_and_is_idempotent() -> None:
    cfg = _field(crop="tomato", crop_map=["tomato"] * 8 + ["cucumber"] * 8)
    filled = with_horizon(cfg)
    assert filled.days == TOMATO
    assert filled.crop_map == cfg.crop_map  # nothing else about it moved
    assert with_horizon(filled) is filled


def test_a_silent_and_an_explicit_horizon_are_the_same_run() -> None:
    """Otherwise the cache holds one copy per spelling of the same field."""
    silent = _field(crop="tomato")
    explicit = silent.model_copy(update={"days": TOMATO})
    assert run_id_for(silent) == run_id_for(explicit)


def test_presets_that_do_name_a_horizon_keep_it() -> None:
    """The drought and disease trials are shorter than wheat on purpose."""
    for key in ("semi-arid", "disease-front"):
        cfg = ScenarioConfig.model_validate(PRESETS[key]["config"])
        assert cfg.days is not None
        assert horizon_days(cfg) == cfg.days < season_days(cfg)


def test_presets_that_stay_silent_get_the_crop_season() -> None:
    for key in ("rabi-wheat", "kharif-maize", "salvador-greenhouse", "salvador-leafy"):
        cfg = ScenarioConfig.model_validate(PRESETS[key]["config"])
        assert cfg.days is None
        assert horizon_days(cfg) == season_days(cfg)


def test_a_mixed_preset_runs_as_long_as_its_slowest_crop(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """End to end: the bundle reports the horizon the crop asked for."""
    from rancho.engine import run_scenario

    cfg = ScenarioConfig.model_validate(PRESETS["salvador-mosaic"]["config"])
    bundle = run_scenario(cfg, tmp_path)
    assert bundle["meta"]["days"] == TOMATO
    assert bundle["meta"]["days"] > CUCUMBER > LETTUCE
