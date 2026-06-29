"""SQLite corpus: accumulate items across collector runs, snapshot engagement over
time (for real velocity), and cache per-item enrichment (embedding + LLM verdict)
so ranking is instant and the collector only judges each item once."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .models import Item

# Columns added on top of the original items table. Applied as guarded ALTERs so
# existing state.db files migrate forward in place.
_EXTRA_COLUMNS = {
    "summary": "TEXT",
    "author": "TEXT",
    "created_at": "TEXT",
    "raw_domain": "TEXT",
    "relevance": "REAL DEFAULT 0",
    "earliness": "REAL DEFAULT 0",
    "reason": "TEXT",
    "tags": "TEXT",          # json list
    "novelty": "REAL DEFAULT 1",
    "enriched": "INTEGER DEFAULT 0",
    "llm_summary": "TEXT",   # cached plain-English readable summary (lazy backfill)
}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


class Store:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS items (
                id          TEXT PRIMARY KEY,
                source      TEXT,
                title       TEXT,
                url         TEXT,
                first_seen  TEXT,
                last_seen   TEXT,
                surfaced    INTEGER DEFAULT 0,
                embedding   TEXT
            );
            CREATE TABLE IF NOT EXISTS engagement (
                id          TEXT,
                ts          TEXT,
                value       REAL,
                PRIMARY KEY (id, ts)
            );
            """
        )
        existing = {r["name"] for r in self.conn.execute("PRAGMA table_info(items)")}
        for col, decl in _EXTRA_COLUMNS.items():
            if col not in existing:
                self.conn.execute(f"ALTER TABLE items ADD COLUMN {col} {decl}")
        # Indexes for the hot queries (created after the ALTERs so `enriched` exists):
        # get_corpus filters enriched+first_seen; enriched_embeddings sorts by last_seen;
        # prune scans last_seen; health groups by source. Guarded, so existing DBs migrate.
        self.conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_items_enriched_first ON items(enriched, first_seen);
            CREATE INDEX IF NOT EXISTS idx_items_enriched_last  ON items(enriched, last_seen);
            CREATE INDEX IF NOT EXISTS idx_items_last_seen      ON items(last_seen);
            CREATE INDEX IF NOT EXISTS idx_items_source         ON items(source);
            """
        )
        self.conn.commit()

    # ---- collection ---------------------------------------------------------
    def upsert_item(self, item: Item) -> None:
        """Insert/refresh item metadata and append an engagement snapshot."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO items
                 (id, source, title, url, summary, author, created_at, raw_domain,
                  first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 last_seen = excluded.last_seen,
                 title     = excluded.title,
                 summary   = excluded.summary""",
            (item.id, item.source, item.title, item.url, item.summary, item.author,
             _iso(item.created_at), item.raw_domain, now, now),
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO engagement (id, ts, value) VALUES (?, ?, ?)",
            (item.id, now, item.engagement),
        )
        # No commit here — collect() batches a whole fetch into one commit (hundreds of
        # per-item fsyncs on the Jetson's flash storage were a real cost). Same-connection
        # reads still see these writes before the commit.

    def commit(self) -> None:
        self.conn.commit()

    def needs_enrichment(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT id, source, title, summary FROM items WHERE enriched = 0"
            ).fetchall()
        )

    def save_enrichment(
        self, item_id: str, embedding: list[float] | None, relevance: float,
        earliness: float, reason: str, tags: list[str], novelty: float,
    ) -> None:
        self.conn.execute(
            """UPDATE items SET embedding = ?, relevance = ?, earliness = ?,
                 reason = ?, tags = ?, novelty = ?, enriched = 1 WHERE id = ?""",
            (json.dumps(embedding) if embedding else None, relevance, earliness,
             reason, json.dumps(tags), novelty, item_id),
        )
        self.conn.commit()

    def set_summary(self, item_id: str, summary: str) -> None:
        """Cache a readable LLM summary for an item (lazy backfill at render time)."""
        self.conn.execute(
            "UPDATE items SET llm_summary = ? WHERE id = ?", (summary, item_id)
        )
        self.conn.commit()

    def has(self, item_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM items WHERE id = ?", (item_id,)
        ).fetchone() is not None

    def enriched_embeddings(self, limit: int = 4000) -> list[list[float]]:
        rows = self.conn.execute(
            "SELECT embedding FROM items WHERE enriched = 1 AND embedding IS NOT NULL "
            "ORDER BY last_seen DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out: list[list[float]] = []
        for r in rows:
            try:
                out.append(json.loads(r["embedding"]))
            except Exception:
                continue
        return out

    # ---- ranking ------------------------------------------------------------
    def engagement_series(self, item_id: str) -> list[tuple[datetime, float]]:
        rows = self.conn.execute(
            "SELECT ts, value FROM engagement WHERE id = ? ORDER BY ts ASC", (item_id,)
        ).fetchall()
        return [(_parse(r["ts"]), r["value"]) for r in rows if _parse(r["ts"])]

    def engagement_endpoints(
        self, ids: list[str]
    ) -> dict[str, tuple[int, datetime | None, float, datetime | None, float]]:
        """First & last engagement snapshot for many ids in ONE query — replaces the
        per-item engagement_series() N+1 in ranking. Velocity only needs the endpoints,
        not the full series. Returns id -> (n, first_ts, first_val, last_ts, last_val)."""
        out: dict[str, tuple[int, datetime | None, float, datetime | None, float]] = {}
        CHUNK = 500  # stay well under SQLite's 999 bound-variable limit
        for i in range(0, len(ids), CHUNK):
            chunk = ids[i:i + CHUNK]
            q = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"""SELECT g.id AS id, g.n AS n, g.first_ts AS first_ts, g.last_ts AS last_ts,
                           ef.value AS first_val, el.value AS last_val
                    FROM (SELECT id, COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts
                          FROM engagement WHERE id IN ({q}) GROUP BY id) g
                    JOIN engagement ef ON ef.id = g.id AND ef.ts = g.first_ts
                    JOIN engagement el ON el.id = g.id AND el.ts = g.last_ts""",
                chunk,
            ).fetchall()
            for r in rows:
                out[r["id"]] = (
                    r["n"], _parse(r["first_ts"]), r["first_val"],
                    _parse(r["last_ts"]), r["last_val"],
                )
        return out

    def get_corpus(
        self, since_hours: float | None = None, with_embeddings: bool = False
    ) -> list[Item]:
        """Rebuild enriched Items for ranking, newest engagement attached.

        The `since` window is applied in SQL (judged by first_seen — when WE first saw
        the item — so freshly-discovered older papers aren't excluded). Embeddings are
        only SELECTed + parsed when `with_embeddings` is set (query mode); a plain `top`
        skips parsing thousands of ~15KB JSON blobs it never uses.
        """
        cols = ("id, source, title, url, summary, author, created_at, raw_domain, "
                "first_seen, relevance, earliness, novelty, reason, tags, llm_summary")
        if with_embeddings:
            cols += ", embedding"
        sql = f"SELECT {cols} FROM items WHERE enriched = 1"
        params: list = []
        if since_hours:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
            sql += " AND (first_seen IS NULL OR first_seen >= ?)"
            params.append(cutoff)
        rows = self.conn.execute(sql, params).fetchall()

        items: list[Item] = []
        for r in rows:
            it = Item(
                source=r["source"], title=r["title"] or "", url=r["url"] or "",
                summary=r["summary"] or "", author=r["author"] or "",
                created_at=_parse(r["created_at"]), raw_domain=r["raw_domain"] or "",
            )
            it._first_seen = _parse(r["first_seen"])  # type: ignore[attr-defined]
            it.relevance = r["relevance"] or 0.0
            it.earliness = r["earliness"] or 0.0
            it.novelty = r["novelty"] if r["novelty"] is not None else 1.0
            it.reason = r["reason"] or ""
            it.llm_summary = r["llm_summary"] or ""
            try:
                it.tags = json.loads(r["tags"]) if r["tags"] else []
            except Exception:
                it.tags = []
            if with_embeddings:
                try:
                    it._embedding = json.loads(r["embedding"]) if r["embedding"] else None  # type: ignore[attr-defined]
                except Exception:
                    it._embedding = None  # type: ignore[attr-defined]
            else:
                it._embedding = None  # type: ignore[attr-defined]
            items.append(it)
        return items

    def prune(self, retention_days: int) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        ids = [r["id"] for r in self.conn.execute(
            "SELECT id FROM items WHERE last_seen < ?", (cutoff,)
        ).fetchall()]
        if ids:
            q = ",".join("?" * len(ids))
            self.conn.execute(f"DELETE FROM items WHERE id IN ({q})", ids)
            self.conn.execute(f"DELETE FROM engagement WHERE id IN ({q})", ids)
            self.conn.commit()
        return len(ids)

    def stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        enriched = self.conn.execute(
            "SELECT COUNT(*) c FROM items WHERE enriched = 1"
        ).fetchone()["c"]
        snaps = self.conn.execute("SELECT COUNT(*) c FROM engagement").fetchone()["c"]
        return {"items": total, "enriched": enriched, "snapshots": snaps}

    def health(self) -> dict:
        """Liveness signal: corpus size + when collection last ran / last found new."""
        row = self.conn.execute(
            "SELECT MAX(last_seen) ls, MAX(first_seen) fs FROM items"
        ).fetchone()
        by_source = {
            r["source"]: r["c"] for r in self.conn.execute(
                "SELECT source, COUNT(*) c FROM items GROUP BY source"
            )
        }
        return {"last_collect": row["ls"], "newest_item": row["fs"],
                "by_source": by_source, **self.stats()}

    def close(self) -> None:
        self.conn.close()
