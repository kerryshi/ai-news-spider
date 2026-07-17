"""Collect-staleness health check: the digest must say so when collection has stopped.

Regression guard for the 2026-07-05 ICS outage — the desktop's ICS dropped, the Jetson's
eth0 lost its lease, every source fetch died, and the 72h ranked window emptied out. The
digest rendered "no items matched" and simply looked like a quiet news day for 74.5h.
Silent staleness is this system's real failure mode, so these tests pin the two things
that make it non-silent: the verdict itself, and the banner surviving the empty-digest
path (where the outage actually shows up).
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from engine import cli
from engine.digest import STALE_AFTER_MINUTES, _stale_banner, collect_health, render_markdown
from engine.models import Item
from engine.store import Store

EXTENSION_SRC = Path(__file__).resolve().parent.parent / "extension" / "src" / "extension.ts"

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
BANNER = "Collection may have stopped"


def _ago(**kw) -> str:
    return (NOW - timedelta(**kw)).isoformat()


def _item(**kw) -> Item:
    it = Item(source=kw.pop("source", "arxiv"), title=kw.pop("title", "t"),
              url=kw.pop("url", "http://x/1"), raw_domain=kw.pop("raw_domain", ""))
    for k, v in kw.items():
        setattr(it, k, v)
    return it


def test_fresh_collect_is_not_stale():
    h = collect_health(_ago(minutes=5), now=NOW)
    assert h["stale"] is False
    assert h["reason"] == "fresh"
    assert h["age_minutes"] == 5


def test_threshold_boundary_is_25_minutes():
    # The collect cron is */20, so 25 min = one missed cycle + slack. At exactly the
    # threshold collection is still considered alive; past it, not.
    assert STALE_AFTER_MINUTES == 25
    assert collect_health(_ago(minutes=25), now=NOW)["stale"] is False
    assert collect_health(_ago(minutes=26), now=NOW)["stale"] is True


def test_outage_duration_is_stale():
    h = collect_health(_ago(hours=74.5), now=NOW)
    assert h["stale"] is True and h["reason"] == "stale"


def test_unknown_last_collect_is_treated_as_stale():
    # Unknown health = unhealthy. A missing stamp cannot prove collection is alive,
    # and reading "unknown" as calm is exactly the silent degradation being guarded.
    h = collect_health(None, now=NOW)
    assert h["stale"] is True
    assert h["reason"] == "unknown"
    assert h["age_minutes"] is None


def test_unparseable_timestamp_is_stale_and_does_not_raise():
    for junk in ("", "not-a-date", "2026-13-45T99:99"):
        h = collect_health(junk, now=NOW)
        assert h["stale"] is True and h["reason"] == "unknown"


def test_naive_timestamp_is_read_as_utc_instead_of_raising():
    # A naive stamp would blow up the tz-aware subtraction and take the whole digest
    # down with it; the corpus writes UTC, so assume UTC rather than crash.
    naive = NOW.replace(tzinfo=None) - timedelta(minutes=5)
    h = collect_health(naive.isoformat(), now=NOW)
    assert h["stale"] is False and h["age_minutes"] == 5


def test_stale_banner_renders_on_an_empty_digest():
    # THE outage shape: staleness empties the ranking window, so the banner has to
    # survive the "no items matched" early return or it misses the real failure.
    md = render_markdown([], health=collect_health(_ago(hours=74.5), now=NOW))
    assert BANNER in md
    assert "74.5 h ago" in md
    assert "No items matched" in md      # the existing empty-state text is preserved


def test_stale_banner_renders_above_the_items():
    md = render_markdown([_item(score=0.5)], health=collect_health(_ago(hours=3), now=NOW))
    assert BANNER in md
    assert md.index(BANNER) < md.index("At a glance")


def test_unknown_banner_names_the_unknown_instead_of_a_bogus_age():
    md = render_markdown([], health=collect_health(None, now=NOW))
    assert BANNER in md
    assert "unknown" in md


def test_fresh_and_absent_health_render_no_banner():
    fresh = collect_health(_ago(minutes=5), now=NOW)
    assert BANNER not in render_markdown([_item(score=0.5)], health=fresh)
    assert BANNER not in render_markdown([], health=fresh)
    # health=None -> existing callers keep their current output, unchanged.
    assert BANNER not in render_markdown([_item(score=0.5)])


def test_store_last_collect_feeds_the_verdict(tmp_path):
    # Wiring: the corpus's own last-collect stamp is what the verdict reads.
    s = Store(tmp_path / "t.db")
    s.upsert_item(_item(url="http://x/9", source="hackernews"))
    s.commit()
    assert collect_health(s.health()["last_collect"])["stale"] is False

    # Age that stamp past the threshold: the same wiring now reports stale.
    old = (datetime.now(timezone.utc) - timedelta(hours=74.5)).isoformat()
    s.conn.execute("UPDATE items SET last_seen = ?", (old,))
    s.commit()
    assert collect_health(s.health()["last_collect"])["stale"] is True
    s.close()


def test_empty_corpus_reports_unknown_not_fresh(tmp_path):
    s = Store(tmp_path / "t.db")
    assert collect_health(s.health()["last_collect"])["reason"] == "unknown"
    s.close()


# ── Extension contract ──────────────────────────────────────────────────────────
# The repo has no TypeScript test harness, and adding one is out of scope, so these
# assert against the extension *source*. They are deliberately narrow: they pin the two
# invariants a reviewer caught the extension breaking, not its runtime behaviour. A real
# behavioural test would need a VS Code harness — noted as a limitation, not a claim.


def _extension_src() -> str:
    return EXTENSION_SRC.read_text(encoding="utf-8")


def _failure_blocks(src: str) -> list[str]:
    """The bodies of the extension's genuine fetch/collect failure paths: both
    `code !== 0` guards, plus showTop's JSON.parse catch (unreadable output = no
    verdict). Deliberately excludes collectNow's stats-line catch — that one runs on
    exit 0, meaning the collect itself SUCCEEDED and only its stats line was unreadable,
    so warning there would be a false alarm rather than a caught outage.
    """
    blocks = re.findall(r"if \(code !== 0\) \{(.*?)\n  \}", src, re.S)
    blocks += [
        b for b in re.findall(r"\} catch \{(.*?)\n  \}", src, re.S)
        if "digest not refreshed" in b
    ]
    # Strip `//` comments: these assertions are about what the code *calls*, and the
    # comments here discuss the very calls being asserted against.
    return [re.sub(r"//[^\n]*", "", b) for b in blocks]


def test_extension_banner_marker_matches_the_engine_headline():
    # The extension prepends this banner only when the engine could not (unreachable
    # collector, or an engine too old to send `health`), and uses the headline as its
    # "already bannered" marker. If the engine's wording drifts from the extension's
    # copy, the guard stops matching and every stale digest gets bannered twice.
    m = re.search(r'const STALE_MARKER = "([^"]+)"', _extension_src())
    assert m, "extension must define STALE_MARKER"
    assert m.group(1) in _stale_banner(collect_health(None, now=NOW))
    assert m.group(1) in _stale_banner(collect_health(_ago(hours=74.5), now=NOW))


def test_extension_failure_paths_never_clear_the_warning():
    # Regression: collectNow() called idleStatus() on failure, which clears
    # backgroundColor — actively erasing an established stale warning and leaving the
    # badge neutral until some later refresh. A failed fetch/collect is evidence FOR
    # staleness; it must never be the thing that quiets the alarm.
    blocks = _failure_blocks(_extension_src())
    assert len(blocks) == 3, f"expected 3 failure paths (top exit, top parse, collect), got {len(blocks)}"
    for b in blocks:
        assert "idleStatus()" not in b
        assert "staleStatus(" in b


def test_extension_failure_paths_warn_on_the_digest_too():
    # Regression: unknown-health paths painted the badge but left the cached digest
    # unchanged — so the open preview still read as a normal digest. That badge/digest
    # split IS the 2026-07-05 failure: the digest looked like a quiet news day.
    for b in _failure_blocks(_extension_src()):
        assert "bannerCachedDigest(" in b


def test_extension_banners_a_digest_from_an_engine_with_no_verdict():
    # An engine too old to send `health` returns valid output with no banner; writing it
    # unchanged would let its silence read as "fresh".
    src = _extension_src()
    assert re.search(
        r"isStale\(result\.health\)\s*\?\s*withStaleBanner\(result\.digest_markdown, result\.health\)",
        src,
    ), "showTop must banner digest_markdown when health is stale/absent"


def test_top_json_carries_the_health_verdict(tmp_path, monkeypatch, capsys):
    # The extension renders the engine's verdict, so `top --json` must ship it.
    monkeypatch.setattr(cli, "run_rank", lambda *a, **k: [])
    monkeypatch.setattr(cli, "attach_summaries", lambda cfg, items, *a, **k: items)
    monkeypatch.setattr(cli, "_collect_health", lambda cfg: collect_health(None, now=NOW))
    monkeypatch.setattr(cli.Config, "digest_dir", property(lambda self: tmp_path))

    assert cli.main(["top", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["health"]["stale"] is True
    assert payload["health"]["reason"] == "unknown"
    assert BANNER in payload["digest_markdown"]


def _run_extension_banner_check(tmp_path, md_input):
    # Behavioral check of the compiled extension: require out/extension.js under a stubbed
    # `vscode` module (its top level only imports/declares) and exercise withStaleBanner.
    import subprocess

    compiled = EXTENSION_SRC.parent.parent / "out" / "extension.js"
    assert compiled.exists(), "compile the extension first (npm run compile)"
    stub_dir = tmp_path / "node_modules" / "vscode"
    stub_dir.mkdir(parents=True)
    (stub_dir / "index.js").write_text("module.exports = {};", encoding="utf-8")
    driver = tmp_path / "driver.js"
    driver.write_text(
        "const ext = require(process.argv[2]).__test;\n"
        "const md = JSON.parse(process.argv[3]);\n"
        "const once = ext.withStaleBanner(md, undefined);\n"
        "const twice = ext.withStaleBanner(once, undefined);\n"
        "console.log(JSON.stringify({once, idempotent: once === twice,\n"
        "  bannered: ext.hasStaleBanner(once)}));\n",
        encoding="utf-8",
    )
    env = dict(os.environ, NODE_PATH=str(tmp_path / "node_modules"))
    out = subprocess.run(
        ["node", str(driver), str(compiled), json.dumps(md_input)],
        capture_output=True, text=True, cwd=tmp_path, timeout=30, env=env,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


def test_extension_banners_digest_whose_content_mentions_the_marker_phrase(tmp_path):
    # Regression (run 2026-07-15_1936, review round 2): detection used a bare substring
    # match on the marker phrase, so a scraped TITLE containing the words suppressed the
    # real banner — an older-engine digest stayed unbannered exactly when it was stale.
    md = f"- [Why '{BANNER}' headlines mislead](https://example.org/post)\n"
    r = _run_extension_banner_check(tmp_path, md)
    assert r["bannered"], "a content mention of the phrase must not suppress the banner"
    assert r["once"].startswith("> \U0001f6a8"), "banner must be prepended as the first line"
    assert md.rstrip("\n") in r["once"], "original digest content must survive"
    assert r["idempotent"], "re-bannering an already-bannered digest must be a no-op"


def test_extension_banner_detection_pins_the_blockquote_structure():
    # The collision fix only holds while detection matches the banner's exact blockquote
    # prefix. If this regexes away, test_extension_banners_digest_whose_content_mentions
    # loses its meaning — pin the construct.
    src = EXTENSION_SRC.read_text(encoding="utf-8")
    assert "STALE_BANNER_PREFIX" in src
    assert re.search(r"line\.startsWith\(STALE_BANNER_PREFIX\)", src)
