"""Two-tier cache for finished runs: memory first, gzipped JSON on disk."""

from __future__ import annotations

import gzip
import json
import zlib
from collections import OrderedDict
from pathlib import Path
from typing import Any

#: How many bundles the memory tier holds.  A bundle is the whole field for
#: every day, so the largest grid the sliders allow ships about 19 MB of JSON —
#: keeping every run ever made in RAM is what turns a long painting session into
#: a swap.  Disk is the real cache; this tier only saves re-reading it.
MEMORY_BUNDLES = 4


class RunStore:
    """Persist bundles so that re-opening the app does not re-simulate."""

    def __init__(
        self,
        root: Path,
        memory_size: int = MEMORY_BUNDLES,
        schema_version: int | None = None,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._memory_size = max(1, memory_size)
        self._memory: OrderedDict[str, dict[str, Any]] = OrderedDict()
        #: When set, a bundle stamped with another version is treated as absent.
        #: The disk cache outlives code upgrades; without this an old-shape run
        #: would be replayed against a client that has moved on.
        self.schema_version = schema_version

    def _bundle_path(self, run_id: str) -> Path:
        return self.root / f"{run_id}.json.gz"

    def _meta_path(self, run_id: str) -> Path:
        return self.root / f"{run_id}.meta.json"

    def _remember(self, run_id: str, bundle: dict[str, Any]) -> None:
        """Keep *bundle* in the memory tier, dropping the least recent one."""
        self._memory[run_id] = bundle
        self._memory.move_to_end(run_id)
        while len(self._memory) > self._memory_size:
            self._memory.popitem(last=False)

    def _stale(self, bundle: dict[str, Any]) -> bool:
        """Whether *bundle* predates the schema this store insists on."""
        return self.schema_version is not None and bundle.get("schema_version") != (
            self.schema_version
        )

    def get(self, run_id: str) -> dict[str, Any] | None:
        """Return a bundle from memory, else from disk, else ``None``.

        A damaged on-disk bundle is a miss, not an error: the cache is
        reconstructible, so a corrupt gzip stream or JSON must send the caller
        back to the simulator rather than surface as a failed request.
        """
        cached = self._memory.get(run_id)
        if cached is not None:
            self._memory.move_to_end(run_id)
            return cached
        path = self._bundle_path(run_id)
        if not path.exists():
            return None
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                bundle: dict[str, Any] = json.load(handle)
        except OSError, EOFError, zlib.error, ValueError:
            return None
        if self._stale(bundle):
            return None
        self._remember(run_id, bundle)
        return bundle

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        """Write *text* so a reader never sees a half-written file.

        Writing straight to the final path means a crash or a full disk partway
        through a large bundle leaves a truncated file the next read chokes on.
        Fill a sibling temp first, then rename — atomic on a single filesystem.
        """
        temp = path.with_name(f"{path.name}.tmp")
        temp.write_text(text, encoding="utf-8")
        temp.replace(path)

    def put(self, bundle: dict[str, Any]) -> None:
        """Store a bundle in memory and on disk (plus a lightweight meta sidecar).

        Both files land atomically, and the sidecar carries the schema version so
        the run listing can drop entries the current code can no longer render.
        """
        run_id = str(bundle["id"])
        self._remember(run_id, bundle)
        bundle_path = self._bundle_path(run_id)
        temp = bundle_path.with_name(f"{bundle_path.name}.tmp")
        with gzip.open(temp, "wt", encoding="utf-8") as handle:
            json.dump(bundle, handle, separators=(",", ":"))
        temp.replace(bundle_path)
        meta = {
            "id": run_id,
            "created_at": bundle["created_at"],
            "schema_version": bundle.get("schema_version"),
            "meta": bundle["meta"],
            "yield": bundle["yield"],
        }
        self._atomic_write(self._meta_path(run_id), json.dumps(meta, ensure_ascii=False))

    def delete(self, run_id: str) -> bool:
        """Drop a run from both tiers. Returns ``True`` if anything was removed."""
        existed = self._memory.pop(run_id, None) is not None
        for path in (self._bundle_path(run_id), self._meta_path(run_id)):
            if path.exists():
                path.unlink()
                existed = True
        return existed

    def summaries(self) -> list[dict[str, Any]]:
        """Summaries of all stored runs, newest first.

        When a schema version is enforced, sidecars stamped with another version
        are skipped: a run recorded by older code would list fine but fail to
        render, which is worse than not offering it at all.
        """
        summaries: list[dict[str, Any]] = []
        for path in self.root.glob("*.meta.json"):
            try:
                summary = json.loads(path.read_text(encoding="utf-8"))
            except OSError, json.JSONDecodeError:
                continue
            if (
                self.schema_version is not None
                and summary.get("schema_version") != self.schema_version
            ):
                continue
            summaries.append(summary)
        summaries.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return summaries
