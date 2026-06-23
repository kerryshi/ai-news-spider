"""Load and expose config.toml as nested dataclass-ish dict access."""

from __future__ import annotations

import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.toml"


class Config:
    def __init__(self, data: dict, root: Path):
        self._d = data
        self.root = root

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        p = Path(path) if path else DEFAULT_CONFIG_PATH
        with open(p, "rb") as f:
            data = tomllib.load(f)
        return cls(data, p.resolve().parent)

    def __getitem__(self, key: str):
        return self._d[key]

    def get(self, *keys, default=None):
        cur = self._d
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    def source(self, name: str) -> dict:
        return self.get("sources", name, default={}) or {}

    def source_enabled(self, name: str) -> bool:
        return bool(self.source(name).get("enabled", False))

    @property
    def digest_dir(self) -> Path:
        d = self.root / self.get("general", "digest_path", default="digests")
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def db_path(self) -> Path:
        return self.root / "state.db"
