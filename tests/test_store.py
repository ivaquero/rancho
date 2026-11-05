"""The run cache — the two things that can silently go wrong in it.

Disk is the cache that matters, so almost all of this store is boring.  The two
parts that are not: a bundle evicted from memory must still come back from disk,
and the memory tier must actually stay bounded — a bundle is the whole field for
every day, and the grid the sliders allow ships roughly 19 MB of JSON, so
"cache everything" is a memory leak with a polite name.
"""

from __future__ import annotations

import json

import pytest

from rancho.frames import MODEL_VERSION, SCHEMA_VERSION
from rancho.store import RunStore, valid_run_id


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


def test_a_damaged_bundle_on_disk_is_a_miss_not_an_error(tmp_path) -> None:
    """A truncated or corrupt gzip stream must send the caller back to the sim.

    The cache is reconstructible, so a half-written ``.json.gz`` is not worth a
    500 — it is simply not there.
    """
    store = RunStore(tmp_path)
    # Bytes that are neither valid gzip nor valid JSON.
    (tmp_path / "deadbeef.json.gz").write_bytes(b"\x1f\x8b not really gzip at all")
    assert store.get("deadbeef") is None


def test_a_stale_schema_version_is_never_served(tmp_path) -> None:
    """Enforcing a version must reject an older-shaped bundle on read."""
    writer = RunStore(tmp_path)
    bundle = _bundle("oldshape")
    bundle["schema_version"] = 99
    writer.put(bundle)
    # The disk copy is there, but a store that wants version 1 will not surface it.
    current = RunStore(tmp_path, schema_version=1)
    assert current.get("oldshape") is None
    # …while a store that does not care still reads it back.
    assert RunStore(tmp_path).get("oldshape") == bundle


def test_the_listing_drops_runs_of_another_schema_version(tmp_path) -> None:
    """A run the client can no longer render must not appear in ``/api/runs``."""
    writer = RunStore(tmp_path)
    fresh = _bundle("fresh")
    fresh["schema_version"] = 1
    writer.put(fresh)
    stale = _bundle("stale")
    stale["schema_version"] = 0
    writer.put(stale)

    assert [item["id"] for item in RunStore(tmp_path, schema_version=1).summaries()] == ["fresh"]
    # No version enforced: everything is listed, stale sidecar included.
    assert {item["id"] for item in RunStore(tmp_path).summaries()} == {"fresh", "stale"}


def test_only_plain_filename_stems_are_valid_run_ids() -> None:
    """A run id is a filename stem, never a path — on Windows ``\\`` is one too."""
    assert valid_run_id("1682e8db2462")
    assert valid_run_id("run-01_A")
    for bad in ("", "..", "..\\outside", "../outside", "a/b", "C:\\tmp\\x", "a.json.gz", "a b"):
        assert not valid_run_id(bad)


def test_an_escaping_run_id_cannot_read_or_delete_outside_root(tmp_path) -> None:
    """The exact regression: DELETE /api/runs/..\\..\\outside removed a stranger."""
    root = tmp_path / "bundles"
    root.mkdir()
    outside = tmp_path / "outside.json.gz"
    outside_meta = tmp_path / "outside.meta.json"
    outside.write_bytes(b"keep me")
    outside_meta.write_text("{}", encoding="utf-8")

    store = RunStore(root)
    assert store.get("..\\outside") is None
    assert store.delete("..\\outside") is False
    assert outside.exists() and outside_meta.exists()


def test_put_refuses_an_unsafe_run_id(tmp_path) -> None:
    """Silently dropping such a bundle would lose the run without a trace."""
    with pytest.raises(ValueError, match="非法运行编号"):
        RunStore(tmp_path).put(_bundle("..\\outside"))


def test_a_stale_model_version_is_never_served_or_listed(tmp_path) -> None:
    """Same wire shape, older physics: the schema stamp alone cannot catch it."""
    writer = RunStore(tmp_path)
    old = _bundle("oldmodel")
    old["schema_version"] = SCHEMA_VERSION
    old["model_version"] = MODEL_VERSION - 1
    writer.put(old)

    current = RunStore(tmp_path, schema_version=SCHEMA_VERSION, model_version=MODEL_VERSION)
    assert current.get("oldmodel") is None
    assert current.summaries() == []
    # A store that enforces neither version still sees the file: the stamp is
    # what excludes it, not the filesystem.
    assert RunStore(tmp_path).get("oldmodel") is not None
