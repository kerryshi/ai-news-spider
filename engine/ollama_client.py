"""Thin Ollama client: embeddings (novelty) + JSON chat (relevance judging).

All local, no API cost. Falls back gracefully if Ollama is unreachable so the
pipeline still produces a heuristic-only digest.
"""

from __future__ import annotations

import json
import math

import httpx


class OllamaClient:
    def __init__(self, host: str, chat_model: str, embed_model: str, timeout: float = 60.0):
        self.host = host.rstrip("/")
        self.chat_model = chat_model
        self.embed_model = embed_model
        self._client = httpx.Client(timeout=timeout)
        self.available = self._ping()

    def _ping(self) -> bool:
        try:
            r = self._client.get(f"{self.host}/api/tags", timeout=5.0)
            return r.status_code == 200
        except Exception:
            return False

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- embeddings -------------------------------------------------------
    def embed(self, text: str) -> list[float] | None:
        if not self.available:
            return None
        try:
            r = self._client.post(
                f"{self.host}/api/embeddings",
                json={"model": self.embed_model, "prompt": text[:4000]},
            )
            r.raise_for_status()
            return r.json().get("embedding")
        except Exception:
            return None

    # ---- json chat --------------------------------------------------------
    def judge(self, system: str, user: str) -> dict | None:
        """Ask the chat model for a strict JSON verdict. Returns parsed dict or None."""
        if not self.available:
            return None
        try:
            r = self._client.post(
                f"{self.host}/api/chat",
                json={
                    "model": self.chat_model,
                    "format": "json",
                    "stream": False,
                    "options": {"temperature": 0.1},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            r.raise_for_status()
            content = r.json()["message"]["content"]
            return json.loads(content)
        except Exception:
            return None

    # ---- plain-text summary ----------------------------------------------
    def summarize(self, system: str, user: str) -> str | None:
        """Free-text completion (no JSON mode) for a readable 1-2 sentence summary."""
        if not self.available:
            return None
        try:
            r = self._client.post(
                f"{self.host}/api/chat",
                json={
                    "model": self.chat_model,
                    "stream": False,
                    "options": {"temperature": 0.2},
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            r.raise_for_status()
            return (r.json()["message"]["content"] or "").strip()
        except Exception:
            return None


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
