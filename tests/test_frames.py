"""The bundle a run turns into — the browser contract, tested without a sim.

:func:`rancho.frames.build_bundle` pivots a verbose CropForge Parquet log into
dense per-cell grids, quantises each channel to ``uint8`` and base64-packs it.
The client decodes those bytes back through a declared ``[low, high]`` domain, so
the two ends have to agree on the codec.  Rather than run a whole season to get a
log, these cases hand ``build_bundle`` a tiny synthetic Parquet dataset and check
the three things that would silently corrupt a render:

* the bundle carries the schema version the store keys on (H4);
* the ``uint8`` base64 channels decode back to the values that went in, within
  the quantisation error the 8-bit domain implies (the roundtrip);
* a log row that falls outside the field or the horizon is dropped, not wrapped
  into a wrong cell — the mask the stage channel used to be missing (M8).
"""

from __future__ import annotations

import base64

import numpy as np
import pandas as pd
import pytest

from rancho.frames import SCHEMA_VERSION, build_bundle
from rancho.scenario import ScenarioConfig

ROWS = 4
COLS = 4
DAYS = 10
CELLS = ROWS * COLS


def _log_dir(tmp_path):
    """Write a minimal plants/soil/environment Parquet log and return it.

    One row per cell per day, plus two deliberately out-of-range rows (a day
    past the horizon and a row past the field) that the scatter must drop.
    """
    log = tmp_path / "cropforge_output" / "session"
    log.mkdir(parents=True)

    day, row, col = (
        np.repeat(np.arange(DAYS), CELLS),
        np.tile(np.repeat(np.arange(ROWS), COLS), DAYS),
        np.tile(np.arange(COLS), ROWS * DAYS),
    )
    cell = row * COLS + col
    lai = (day + cell).astype(float)

    plants = pd.DataFrame(
        {
            "day": day + 1,
            "row": row,
            "col": col,
            "lai": lai,
            "biomass_g": lai * 2.0,
            "height_cm": lai,
            "stress_index": np.zeros(CELLS * DAYS),
            "disease_severity": np.zeros(CELLS * DAYS),
            "phenological_stage": np.where(lai > 3.0, "seedling", "germination"),
            "alive": np.ones(CELLS * DAYS, dtype=bool),
        }
    )
    # The out-of-range rows: one past the horizon (fails the day bound) and one
    # on a row past the field (fails the cell-index bound).  Both must be masked
    # out, never wrapped into a real cell.
    stray = pd.DataFrame(
        {
            "day": [DAYS + 5, 1],
            "row": [0, ROWS + 2],
            "col": [0, 0],
            "lai": [999.0, 999.0],
            "biomass_g": [999.0, 999.0],
            "height_cm": [999.0, 999.0],
            "stress_index": [999.0, 999.0],
            "disease_severity": [999.0, 999.0],
            "phenological_stage": ["maturity", "maturity"],
            "alive": [True, True],
        }
    )
    plants = pd.concat([plants, stray], ignore_index=True)
    plants.to_parquet(log / "plants")

    soil = pd.DataFrame(
        {
            "day": day + 1,
            "row": row,
            "col": col,
            "layer": 0,
            "moisture_pct": np.full(CELLS * DAYS, 40.0),
            "nitrogen_kg_ha": np.full(CELLS * DAYS, 20.0),
        }
    )
    soil.to_parquet(log / "soil")

    environment = pd.DataFrame(
        {
            "day": np.arange(DAYS) + 1,
            "doy": np.arange(DAYS) + 1,
            "temp_mean_c": np.full(DAYS, 18.0),
            "rainfall_mm": np.zeros(DAYS),
            "et0_mm": np.full(DAYS, 4.0),
        }
    )
    environment.to_parquet(log / "environment")
    return log


def _config() -> ScenarioConfig:
    return ScenarioConfig(
        name="Codec",
        crop="tomato",
        rows=ROWS,
        cols=COLS,
        days=DAYS,
        irrigation=False,
        soil_pattern="uniform",
        soil_variation=0.0,
    )


def test_the_bundle_is_stamped_with_the_schema_version(tmp_path) -> None:
    log = _log_dir(tmp_path)
    bundle = build_bundle(
        cfg=_config(),
        log_dir=log,
        elevation=np.zeros((ROWS, COLS)),
        yield_summary=None,
        elapsed_s=0.0,
        run_id="deadbeef",
        created_at="2026-01-01T00:00:00+00:00",
    )
    assert bundle["schema_version"] == SCHEMA_VERSION


def test_a_channel_round_trips_through_the_uint8_codec(tmp_path) -> None:
    """What the client decodes must match what frames encoded, within 8-bit error.

    Reproduces the client's ``decodeChannel`` inverse: ``low + byte * span``,
    ``span = (high - low) / 255``.  A codec drift between the two ends is exactly
    the kind of silent colour breakage the schema version guards against.
    """
    log = _log_dir(tmp_path)
    bundle = build_bundle(
        cfg=_config(),
        log_dir=log,
        elevation=np.zeros((ROWS, COLS)),
        yield_summary=None,
        elapsed_s=0.0,
        run_id="deadbeef",
        created_at="2026-01-01T00:00:00+00:00",
    )
    low, high = bundle["channels"]["lai"]["domain"]
    span = (high - low) / 255.0
    frame0 = base64.b64decode(bundle["frames"][0]["ch"]["lai"])
    decoded = np.array([low + b * span for b in frame0], dtype=float)
    # Day 0 (index 0) of every cell: lai == cell for that day.
    expected = np.arange(CELLS, dtype=float)
    assert decoded.shape == (CELLS,)
    # The worst error a uint8 quantiser over this domain can introduce.
    assert np.allclose(decoded, expected, atol=span + 1e-6)


def test_an_out_of_range_log_row_never_wraps_into_a_cell(tmp_path) -> None:
    """M8: the stray day and row must be dropped, not written to a real cell.

    Without the bounds mask the ``day = DAYS + 5`` row indexed past the grid and
    the ``row = ROWS + 2`` row folded onto a legitimate cell, painting a bogus
    stage value where a real plant is.  Cell (0, 0) on day 1 must stay germination
    (stage index 0), never maturity.
    """
    log = _log_dir(tmp_path)
    bundle = build_bundle(
        cfg=_config(),
        log_dir=log,
        elevation=np.zeros((ROWS, COLS)),
        yield_summary=None,
        elapsed_s=0.0,
        run_id="deadbeef",
        created_at="2026-01-01T00:00:00+00:00",
    )
    # The stage grid is the last channel; day 0, cell 0 held germination (0.0)
    # with a low domain bound of 0, so its first byte quantises to the domain's
    # low end.  A wrapped "maturity" row would have driven it to the high end.
    low, high = bundle["channels"]["stage"]["domain"]
    assert high > low  # both stages present, so the domain spans them
    stage0 = base64.b64decode(bundle["frames"][0]["ch"]["stage"])[0]
    decoded = low + stage0 * ((high - low) / 255.0)
    assert decoded == pytest.approx(low, abs=(high - low) / 255.0)
