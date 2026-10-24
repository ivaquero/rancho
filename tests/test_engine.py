"""The one engine this process has, and what a second caller gets.

CropForge writes its Parquet log relative to the process working directory, so
runs cannot overlap and :func:`rancho.engine.run_scenario` serialises them.  The
question this file answers is what happens to the caller that arrives second:
queueing behind a minute-long run is indistinguishable from a hung server, so
the HTTP layer asks not to wait and expects to be refused.
"""

from __future__ import annotations

import threading

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
    with (
        _engine_slot(),  # one run is in progress and owns the engine
        pytest.raises(RunBusyError, match="已有仿真在运行"),
        _engine_slot(wait=False),  # so this one has to be refused, not queued
    ):
        pass


def test_a_second_caller_can_still_wait_its_turn() -> None:
    """The CLI wants the opposite: queue, and do not lose the run.

    This only makes sense across threads — the lock is not reentrant, so asking
    to wait from *inside* a run would deadlock the caller with itself.
    """
    ran = threading.Event()
    entered = threading.Event()

    def holder() -> None:
        with _engine_slot():
            entered.set()
            ran.wait(5.0)

    thread = threading.Thread(target=holder)
    thread.start()
    assert entered.wait(5.0), "the holding run never started"

    with _engine_slot(wait=True):  # blocks until the thread leaves
        pass
    ran.set()
    thread.join(5.0)
