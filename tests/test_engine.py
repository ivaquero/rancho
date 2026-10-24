"""The one engine this process has, and what a second caller gets.

CropForge writes its Parquet log relative to the process working directory, so
runs cannot overlap and :func:`rancho.engine.run_scenario` serialises them.  The
question this file answers is what happens to the caller that arrives second:
queueing behind a minute-long run is indistinguishable from a hung server, so
the HTTP layer asks not to wait and expects to be refused.
"""

from __future__ import annotations

import pytest

from rancho.engine import RunBusyError, _engine_slot


def test_the_slot_is_handed_back_after_a_run() -> None:
    with _engine_slot():
        pass
    # If the release were missing, this raises instead of running.
    with _engine_slot(wait=False):
        pass


def test_a_second_caller_is_refused_rather_than_queued() -> None:
    """``wait=False`` must fail fast — a queued request looks like a hang."""
    with _engine_slot():
        with pytest.raises(RunBusyError, match="已有仿真在运行"):
            with _engine_slot(wait=False):
                pass


def test_a_second_caller_can_still_wait_its_turn() -> None:
    """The CLI wants the opposite: queue, and do not lose the run."""
    with _engine_slot():
        with _engine_slot(wait=True):
            pass
