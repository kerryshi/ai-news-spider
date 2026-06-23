"""CLI entrypoint.

    python -m engine.cli collect              # scrape + enrich into the corpus (timer job)
    python -m engine.cli top                  # rank the corpus, write+print a digest
    python -m engine.cli top --query "agents" --since 24h --n 15
    python -m engine.cli top --json           # {items, digest_markdown} for the extension
    python -m engine.cli run                  # collect, then top (one-shot)
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import Config
from .digest import render_markdown, write
from .pipeline import collect as run_collect, rank as run_rank


def _parse_since(s: str | None) -> float | None:
    if not s:
        return None
    s = s.strip().lower()
    try:
        if s.endswith("h"):
            return float(s[:-1])
        if s.endswith("d"):
            return float(s[:-1]) * 24
        return float(s)
    except ValueError:
        return None


def _progress(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _emit_top(cfg: Config, query, since_hours, n, as_json: bool) -> None:
    items = run_rank(cfg, query=query, since_hours=since_hours, n=n)
    bits = []
    if query:
        bits.append(f"topic: {query}")
    if since_hours:
        bits.append(f"last {since_hours:g}h")
    subtitle = " · ".join(bits)
    md = render_markdown(items, subtitle)
    md_path, _ = write(items, cfg.digest_dir)
    _progress(f"✓ ranked {len(items)} items → {md_path}")

    if as_json:
        print(json.dumps(
            {"digest_md": str(md_path), "digest_markdown": md,
             "items": [it.to_dict() for it in items]},
            indent=2, default=str,
        ))
    else:
        for i, it in enumerate(items[:10], 1):
            print(f"{i:2}. [{it.score:.3f}] {it.source:11} {it.title[:80]}")
        print(f"\nFull digest: {md_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-signal")
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("collect", help="scrape and enrich into the corpus")

    p_top = sub.add_parser("top", help="rank the corpus and write a digest")
    p_top.add_argument("--query", default=None, help="topic to focus on")
    p_top.add_argument("--since", default=None, help="window, e.g. 24h or 3d")
    p_top.add_argument("--n", type=int, default=None, help="number of items")
    p_top.add_argument("--json", action="store_true")

    p_run = sub.add_parser("run", help="collect, then top (one-shot)")
    p_run.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    cfg = Config.load(args.config)

    if args.cmd == "collect":
        _progress("Collecting…")
        stats = run_collect(cfg, _progress)
        print(json.dumps(stats))
        return 0

    if args.cmd == "top":
        _emit_top(cfg, args.query, _parse_since(args.since), args.n, args.json)
        return 0

    if args.cmd == "run":
        _progress("Collecting…")
        run_collect(cfg, _progress)
        _emit_top(cfg, None, None, None, args.json)
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
