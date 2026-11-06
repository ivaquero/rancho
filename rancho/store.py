"""Two-tier cache for finished runs: memory first, gzipped JSON on disk."""

from __future__ import annotations

import gzip
import json
import re
import zlib
from collections import OrderedDict
from pathlib import Path
from typing import Any

#: How many bundles the memory tier holds.  A bundle is the whole field for
#: every day, so the largest grid the sliders allow ships about 19 MB of JSON —
#: keeping every run ever made in RAM is what turns a long painting session into
#: a swap.  Disk is the real cache; this tier only saves re-reading it.
MEMORY_BUNDLES = 4

#: A run id is a filename stem the engine generates (12 hex chars today).
#: Accepting anything else lets a URL path parameter walk out of ``root`` with
#: ``..\`` or an absolute path and read or delete files the cache does not own.
_RUN_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def valid_run_id(run_id: str) -> bool:
    """Whether *run_id* can only name a file inside the cache directory."""
    return _RUN_ID_RE.fullmatch(run_id) is not None


class RunStore:
    """Persist bundles so that re-opening the app does not re-simulate."""

    def __init__(
        self,
        root: Path,
        memory_size: int = MEMORY_BUNDLES,
        schema_version: int | None = None,
        model_version: int | None = None,
    ) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._memory_size = max(1, memory_size)
        self._memory: OrderedDict[str, dict[str, Any]] = OrderedDict()
        #: When set, a bundle stamped with another version is treated as absent.
        #: The disk cache outlives code upgrades; without this an old-shape run
        #: would be replayed against a client that has moved on.
        self.schema_version = schema_version
        #: Same idea for the physics.  Two runs with a different water-stress
        #: formula have the *same* wire shape, so the schema stamp cannot tell
        #: them apart — only an explicit model stamp keeps old numbers out.
        self.model_version = model_version

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
        """Whether *bundle* predates the schema or model this store insists on."""
        return (
            self.schema_version is not None and bundle.get("schema_version") != self.schema_version
        ) or (self.model_version is not None and bundle.get("model_version") != self.model_version)

    def get(self, run_id: str) -> dict[str, Any] | None:
        """Return a bundle from memory, else from disk, else ``None``.

        A damaged on-disk bundle is a miss, not an error: the cache is
        reconstructible, so a corrupt gzip stream or JSON must send the caller
        back to the simulator rather than surface as a failed request.
        """
        if not valid_run_id(run_id):
            return None
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

    def _read_meta(self, run_id: str) -> dict[str, Any] | None:
        """Parse the small ``.meta.json`` sidecar, or ``None`` if it is absent."""
        try:
            text = self._meta_path(run_id).read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            parsed: Any = json.loads(text)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def get_gz(self, run_id: str) -> bytes | None:
        """Return a stored bundle as raw gzip bytes, ready to replay verbatim.

        A cache hit served this way skips two costs the ordinary ``get`` pays:
        re-inflating and re-parsing a multi-megabyte bundle, and the response
        layer re-compressing JSON that is already gzipped on disk.  We read the
        ``.json.gz`` straight through and hand it to a ``Content-Encoding: gzip``
        response untouched.  Freshness is judged from the tiny sidecar instead of
        inflating the whole bundle, so a stale run never leaks through.  Anything
        that is not a clean fresh hit — bad id, missing or corrupt sidecar, stale
        stamp, unreadable bundle — returns ``None`` so the caller falls back to
        the ordinary path and stays correct.
        """
        if not valid_run_id(run_id):
            return None
        meta = self._read_meta(run_id)
        if meta is None or self._stale(meta):
            return None
        try:
            return self._bundle_path(run_id).read_bytes()
        except OSError:
            return None

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
        if not valid_run_id(run_id):
            # Only the engine produces ids, so this is a programming error,
            # not a request to shrug off: writing outside root loses the run.
            raise ValueError(f"非法运行编号：{run_id!r}")
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
            "model_version": bundle.get("model_version"),
            "meta": bundle["meta"],
            "yield": bundle["yield"],
        }
        self._atomic_write(self._meta_path(run_id), json.dumps(meta, ensure_ascii=False))

    def delete(self, run_id: str) -> bool:
        """Drop a run from both tiers. Returns ``True`` if anything was removed."""
        if not valid_run_id(run_id):
            return False
        existed = self._memory.pop(run_id, None) is not None
        for path in (self._bundle_path(run_id), self._meta_path(run_id)):
            if path.exists():
                path.unlink()
                existed = True
        return existed

    def summaries(self) -> list[dict[str, Any]]:
        """Summaries of all stored runs, newest first.

        When a schema or model version is enforced, sidecars stamped with another
        version are skipped: a run recorded by older code would list fine but
        fail to render — or worse, silently show numbers from an older model.
        """
        summaries: list[dict[str, Any]] = []
        for path in self.root.glob("*.meta.json"):
            try:
                summary = json.loads(path.read_text(encoding="utf-8"))
            except OSError, json.JSONDecodeError:
                continue
            if self._stale(summary):
                continue
            summaries.append(summary)
        summaries.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return summaries
