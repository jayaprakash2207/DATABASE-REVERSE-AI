"""
File-based parse cache.  Cache key = SHA-256 of file content.
Avoids re-parsing unchanged files across runs.
"""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Optional


class FileCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._hits = 0
        self._misses = 0

    # ------------------------------------------------------------------

    def _key(self, source_path: Path) -> str:
        try:
            content = source_path.read_bytes()
            return hashlib.sha256(content).hexdigest()[:20]
        except OSError:
            return hashlib.sha256(str(source_path).encode()).hexdigest()[:20]

    def _cache_path(self, source_path: Path, parser: str) -> Path:
        return self.cache_dir / f"{parser}_{self._key(source_path)}.json"

    # ------------------------------------------------------------------

    def get(self, source_path: Path, parser: str) -> Optional[dict]:
        cp = self._cache_path(source_path, parser)
        if cp.exists():
            try:
                data = json.loads(cp.read_text(encoding="utf-8"))
                self._hits += 1
                return data
            except Exception:
                pass
        self._misses += 1
        return None

    def set(self, source_path: Path, parser: str, data: dict) -> None:
        cp = self._cache_path(source_path, parser)
        try:
            cp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass

    def invalidate(self, source_path: Path, parser: str) -> None:
        self._cache_path(source_path, parser).unlink(missing_ok=True)

    def clear_all(self) -> None:
        for f in self.cache_dir.glob("*.json"):
            f.unlink(missing_ok=True)

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        rate = self._hits / total if total else 0.0
        return {"hits": self._hits, "misses": self._misses, "hit_rate": round(rate, 3)}
