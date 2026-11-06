"""Anti-corruption layer around CropForge's private and unstable surface.

CropForge is a third-party engine we do not control, and it exposes exactly the
kind of things a running system should touch *once*: a private plugin registry
(``cropforge._plugins_base``), a logging class with no supported switch to
silence it, a field-state initialiser tucked behind an underscore, and an
on-disk session layout that is an implementation detail of ``Farm.run``.

Every one of those lives here and nowhere else.  When CropForge moves a name,
this module is the single point of failure — and, more importantly, the single
point of repair.  The rest of Rancho imports the friendly wrappers below and
stays ignorant of what actually changed upstream.
"""

from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final, Protocol

from cropforge import Field
from cropforge._plugins_base import CropPlugin, register_crop
from cropforge.logger import StateLogger

__all__ = [
    "RUN_LOCK",
    "CropPlugin",
    "init_field_state",
    "latest_session_dir",
    "quiet_state_logger",
    "register_crop",
]

#: CropForge's ``StateLogger`` resolves its Parquet log from the *process*
#: working directory, so a run must own that directory while it lasts — and the
#: logger-quieting in :func:`quiet_state_logger` is likewise process-global.
#: Both the HTTP engine and the offline ``econ`` probe take this one lock, so a
#: web run can never have its working directory yanked out from under it or,
#: worse, silently inherit a probe's quieted logger from a concurrent run.
RUN_LOCK: Final = threading.Lock()

#: ``Farm.run`` writes its partitioned Parquet log under this fixed directory,
#: relative to the *process* working directory.  The name is not configurable,
#: which is why a run has to own the working directory while it lasts.
OUTPUT_ROOT: Final = "cropforge_output"


def latest_session_dir(work_dir: Path) -> Path:
    """Return the newest session directory CropForge wrote under *work_dir*.

    ``Farm.run`` creates one timestamped session directory per call, so the run
    that just finished is the last one once the names are sorted.  A missing log
    is a broken run, not an empty one, so this raises rather than handing back a
    path that would only fail later when someone tries to read it.
    """
    sessions = sorted((work_dir / OUTPUT_ROOT).glob("*"))
    if not sessions:
        raise RuntimeError("CropForge produced no state log for this run.")
    return sessions[-1]


class PlantState(Protocol):
    """The shape of one plant inside a field state.

    Only the attributes the transplanted-seedling start touches are declared —
    enough to make :func:`init_field_state` return something checkable instead of
    ``Any`` while staying decoupled from CropForge's own (private) state classes.
    """

    lai: float
    phenological_stage: str
    custom: dict[str, object]


class FieldState(Protocol):
    """A CropForge field state: the plants a season starts from."""

    plants: list[PlantState]


def init_field_state(field_: Field, day: int = 1) -> FieldState:
    """Build the field state a season starts from, through CropForge's private seam.

    ``Field._init_field_state`` is the very hook CropForge's own
    ``prepare_next_season`` uses to seed a canopy before the first day's
    radiation; there is no public equivalent.  If that private name ever moves,
    this fails loudly with the exact name to go look for instead of silently
    growing a season from a seed.
    """
    initialise = getattr(field_, "_init_field_state", None)
    if initialise is None:
        raise RuntimeError(
            "CropForge no longer exposes Field._init_field_state; "
            "the transplanted-seedling start cannot be applied."
        )
    return initialise(day=day)


class _Unlogged(StateLogger):
    """The session logger, minus the session.

    Every ``Farm.run`` builds a partitioned Parquet dataset of the whole field
    and the market probe deletes it unread one call later.  At this field size
    encoding it costs about twelve times what the physics it records costs, plus
    the same again to delete the temporary tree, so the two methods that produce
    it are stubbed out for the run.  Everything else is inherited: the session
    directory, ``log_path`` and the terrain dump still happen, and any consumer
    of the log would simply find an empty dataset.

    This is a shortcut around CropForge's logging API, not a supported switch —
    if the probe ever needs the log back, delete this class and the guard that
    installs it.
    """

    def record(self, *args: Any, **kwargs: Any) -> None:
        return None

    def flush(self) -> None:
        return None


@contextmanager
def quiet_state_logger() -> Generator[None]:
    """Install :class:`_Unlogged` where ``Farm.run`` imports ``StateLogger`` from.

    The patch is on the module attribute, not the class, so it only lives for the
    duration of the block and is restored on the way out — even if the run raises.
    """
    import cropforge.logger as logger_module

    saved = logger_module.StateLogger
    logger_module.StateLogger = _Unlogged  # ty: ignore[invalid-assignment] - the patch
    try:
        yield
    finally:
        logger_module.StateLogger = saved
