"""The run cache — the two things that can silently go wrong in it.

Disk is the cache that matters, so almost all of this store is boring.  The two
parts that are not: a bundle evicted from memory must still come back from disk,
and the memory tier must actually stay bounded — a bundle is the whole field for
every day, and the grid the sliders allow ships roughly 19 MB of JSON, so
"cache everything" is a memory leak with a polite name.
"""

from __future__ import annotations

import json

from rancho.store import RunStore


def _bundle(run_id: str) -> dict:
    return {
        "id": run_id,
        "created_at": "2026-01-01T00:00:00+00:00",
        "meta": {"rows": 4, "cols": 4},
        "yield": {"t_per_ha": 1.0},
        "frames": [{"day": 1}],
    }


def test_a_bundle_survives_being_evicted_from_memory(tmp_path) -> None:
    """Eviction is a memory-tier decision only; the disk copy is still the truth."""
    store = RunStore(tmp_path, memory_size=1)
    store.put(_bundle("aaaa"))
    store.put(_bundle("bbbb"))

    assert len(store._memory) == 1
    # "aaaa" was pushed out, but reading it back must not miss.
    assert store.get("aaaa") == _bundle("aaaa")


def test_the_memory_tier_stays_bounded(tmp_path) -> None:
    store = RunStore(tmp_path, memory_size=3)
    for index in range(10):
        store.put(_bundle(f"run{index:02d}"))
    assert len(store._memory) == 3
    # The three most recent, in order: anything else means the eviction picked
    # the wrong end.
    assert list(store._memory) == ["run07", "run08", "run09"]


def test_reading_a_run_from_disk_refreshes_it(tmp_path) -> None:
    """A hit on disk is a hit on memory next time, and counts as recent."""
    store = RunStore(tmp_path, memory_size=2)
    store.put(_bundle("aaaa"))
    store.put(_bundle("bbbb"))
    store.get("aaaa")  # evicted, so this reads the gzipped copy back in
    store.put(_bundle("cccc"))

    # "aaaa" was touched last, so "bbbb" is what goes.
    assert list(store._memory) == ["aaaa", "cccc"]


def test_deleting_a_run_drops_both_copies(tmp_path) -> None:
    store = RunStore(tmp_path, memory_size=2)
    store.put(_bundle("aaaa"))
    assert store.delete("aaaa") is True
    assert store.get("aaaa") is None
    assert store.delete("aaaa") is False


def test_a_corrupt_meta_file_does_not_break_the_listing(tmp_path) -> None:
    store = RunStore(tmp_path)
    store.put(_bundle("good"))
    (tmp_path / "bad.meta.json").write_text("{ not json", encoding="utf-8")
    summaries = store.summaries()
    assert [item["id"] for item in summaries] == ["good"]


def test_summaries_are_newest_first(tmp_path) -> None:
    store = RunStore(tmp_path)
    for index, stamp in enumerate(("2026-01-01T00:00:00+00:00", "2026-06-01T00:00:00+00:00")):
        bundle = _bundle(f"run{index}")
        bundle["created_at"] = stamp
        store.put(bundle)
    assert [item["id"] for item in store.summaries()] == ["run1", "run0"]
    # The sidecar is what the listing reads, so it must stay small.
    sidecar = json.loads((tmp_path / "run0.meta.json").read_text(encoding="utf-8"))
    assert "frames" not in sidecar
