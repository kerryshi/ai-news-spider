"""Corpus store — schema migration, the enrich-once contract, snapshots, prune."""

from engine.models import Item
from engine.store import Store


def _it(url, eng=0.0, src="hackernews", title="LLM agent model"):
    return Item(source=src, title=title, url=url, engagement=eng)


def test_upsert_and_enrich_once(tmp_path):
    s = Store(tmp_path / "t.db")
    it = _it("http://x/1", eng=10.0)
    s.upsert_item(it)

    assert s.has(it.id)
    assert s.stats()["items"] == 1
    # newly inserted -> pending enrichment
    assert [r["id"] for r in s.needs_enrichment()] == [it.id]

    s.save_enrichment(it.id, [0.1, 0.2, 0.3], relevance=8.0, earliness=7.0,
                      reason="why", tags=["llm"], novelty=0.9)
    # enriched items are never re-queued (the "enrich once" contract)
    assert s.needs_enrichment() == []

    corpus = s.get_corpus()
    assert len(corpus) == 1
    assert corpus[0].relevance == 8.0
    assert corpus[0].tags == ["llm"]
    s.close()


def test_snapshots_accumulate_for_velocity(tmp_path):
    s = Store(tmp_path / "t.db")
    it = _it("http://x/2", eng=5.0)
    s.upsert_item(it)
    series1 = s.engagement_series(it.id)
    assert series1 and series1[-1][1] == 5.0
    s.close()


def test_get_corpus_only_returns_enriched(tmp_path):
    s = Store(tmp_path / "t.db")
    s.upsert_item(_it("http://x/3"))            # not enriched
    enriched = _it("http://x/4")
    s.upsert_item(enriched)
    s.save_enrichment(enriched.id, None, 5.0, 5.0, "", [], 1.0)
    corpus = s.get_corpus()
    assert [c.url for c in corpus] == ["http://x/4"]
    s.close()


def test_since_hours_filters_by_first_seen(tmp_path):
    s = Store(tmp_path / "t.db")
    it = _it("http://x/5")
    s.upsert_item(it)
    s.save_enrichment(it.id, None, 5.0, 5.0, "", [], 1.0)
    # backdate first_seen well beyond the window
    s.conn.execute("UPDATE items SET first_seen=? WHERE id=?",
                   ("2000-01-01T00:00:00+00:00", it.id))
    s.conn.commit()
    assert s.get_corpus(since_hours=72) == []      # excluded: discovered long ago
    assert len(s.get_corpus(since_hours=None)) == 1  # no window -> included
    s.close()


def test_prune_removes_old_items(tmp_path):
    s = Store(tmp_path / "t.db")
    it = _it("http://x/6")
    s.upsert_item(it)
    s.conn.execute("UPDATE items SET last_seen=? WHERE id=?",
                   ("2000-01-01T00:00:00+00:00", it.id))
    s.conn.commit()
    assert s.prune(retention_days=14) == 1
    assert s.stats()["items"] == 0
    s.close()


def test_schema_migrates_in_place(tmp_path):
    """An old-style items table (without the new columns) must upgrade silently."""
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE items (id TEXT PRIMARY KEY, source TEXT, title TEXT, url TEXT, "
        "first_seen TEXT, last_seen TEXT, surfaced INTEGER, embedding TEXT);"
        "CREATE TABLE engagement (id TEXT, ts TEXT, value REAL, PRIMARY KEY(id,ts));"
    )
    con.commit()
    con.close()

    s = Store(db)  # should ALTER in the missing columns without error
    s.upsert_item(_it("http://x/7"))
    assert s.stats()["items"] == 1
    s.close()
