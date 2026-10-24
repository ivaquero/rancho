"""Two-tier cache for finished runs: memory first, gzipped JSON on disk."""

from __future__ import annotations

import gzip
import json
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

    def __init__(self, root: Path, memory_size: int = MEMORY_BUNDLES) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._memory_size = max(1, memory_size)
        self._memory: OrderedDict[str, dict[str, Any]] = OrderedDict()

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

    def get(self, run_id: str) -> dict[str, Any] | None:
        """Return a bundle from memory, else from disk, else ``None``."""
        cached = self._memory.get(run_id)
        if cached is not None:
            self._memory.move_to_end(run_id)
            return cached
        path = self._bundle_path(run_id)
        if not path.exists():
            return None
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            bundle: dict[str, Any] = json.load(handle)
        self._remember(run_id, bundle)
        return bundle

    def put(self, bundle: dict[str, Any]) -> None:
        """Store a bundle in memory and on disk (plus a lightweight meta sidecar)."""
        run_id = str(bundle["id"])
        self._remember(run_id, bundle)
        with gzip.open(self._bundle_path(run_id), "wt", encoding="utf-8") as handle:
            json.dump(bundle, handle, separators=(",", ":"))
        meta = {
            "id": run_id,
            "created_at": bundle["created_at"],
            "meta": bundle["meta"],
            "yield": bundle["yield"],
        }
        self._meta_path(run_id).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    def delete(self, run_id: str) -> bool:
        """Drop a run from both tiers. Returns ``True`` if anything was removed."""
        existed = self._memory.pop(run_id, None) is not None
        for path in (self._bundle_path(run_id), self._meta_path(run_id)):
            if path.exists():
                path.unlink()
                existed = True
        return existed

    def summaries(self) -> list[dict[str, Any]]:
        """Summaries of all stored runs, newest first."""
        summaries: list[dict[str, Any]] = []
        for path in self.root.glob("*.meta.json"):
            try:
                summaries.append(json.loads(path.read_text(encoding="utf-8")))
            except OSError, json.JSONDecodeError:
                continue
        summaries.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return summaries
