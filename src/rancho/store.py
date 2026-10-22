"""Two-tier cache for finished runs: memory first, gzipped JSON on disk."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any


class RunStore:
    """Persist bundles so that re-opening the app does not re-simulate."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._memory: dict[str, dict[str, Any]] = {}

    def _bundle_path(self, run_id: str) -> Path:
        return self.root / f"{run_id}.json.gz"

    def _meta_path(self, run_id: str) -> Path:
        return self.root / f"{run_id}.meta.json"

    def get(self, run_id: str) -> dict[str, Any] | None:
        """Return a bundle from memory, else from disk, else ``None``."""
        if run_id in self._memory:
            return self._memory[run_id]
        path = self._bundle_path(run_id)
        if not path.exists():
            return None
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            bundle: dict[str, Any] = json.load(handle)
        self._memory[run_id] = bundle
        return bundle

    def put(self, bundle: dict[str, Any]) -> None:
        """Store a bundle in memory and on disk (plus a lightweight meta sidecar)."""
        run_id = str(bundle["id"])
        self._memory[run_id] = bundle
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
            except (OSError, json.JSONDecodeError):
                continue
        summaries.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return summaries
