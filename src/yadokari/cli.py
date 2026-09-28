"""コマンド。

    python -m yadokari.cli db migrate
    python -m yadokari.cli collect [--source archdaily] [--limit 5] [--dry-run]
    python -m yadokari.cli score [--limit 40]
    python -m yadokari.cli list [--status scored] [--min-score 50]
    python -m yadokari.cli stats
"""

from __future__ import annotations

import argparse
import json
import sys

from yadokari.config import load_config
from yadokari.db.connection import connect
from yadokari.db.migrate import ensure_migrated, migrate
from yadokari.db.repository import counts_by_source, list_articles
from yadokari.logging_setup import setup_logging


def _cmd_db(cfg, args) -> int:
    migrate(cfg.app.target())
    print("OK")
    return 0


def _cmd_collect(cfg, args) -> int:
    from yadokari.collect.runner import collect_all

    conn = None
    if not args.dry_run:
        ensure_migrated(cfg.app.target())
        conn = connect(cfg.app.target())
    try:
        results = collect_all(cfg, conn, names=args.source, limit=args.limit, dry_run=args.dry_run)
    finally:
        if conn is not None:
            conn.close()
    for s in results:
        print(s.summary())
        if args.dry_run:
            for u in s.inserted_urls:
                print("   ", u)
    return 0


def _cmd_score(cfg, args) -> int:
    from yadokari.scoring.runner import score_pending

    ensure_migrated(cfg.app.target())
    conn = connect(cfg.app.target())
    try:
        stats = score_pending(cfg, conn, limit=args.limit)
    finally:
        conn.close()
    print(stats.summary())
    return 0 if stats.failed == 0 else 1


def _cmd_list(cfg, args) -> int:
    conn = connect(cfg.app.target())
    try:
        rows = list_articles(conn, status=args.status, min_score=args.min_score, limit=args.limit)
    finally:
        conn.close()
    for r in rows:
        score = f"{r['score']:5.1f}" if r["score"] is not None else "  -  "
        kind = ""
        if r["assessment"]:
            a = json.loads(r["assessment"])
            kind = f"{a.get('kind', '')} {a.get('facts', {}).get('country', '')}"
        print(f"{score} [{r['status']:9}] {r['source']:14} {r['image_count']:>2}枚 {kind:28} {r['title'] or ''}"[:200])
        print(f"      {r['source_url']}")
    return 0


def _cmd_stats(cfg, args) -> int:
    conn = connect(cfg.app.target())
    try:
        for r in counts_by_source(conn):
            print(f"{r['source']:16} {r['status']:10} {r['n']}")
    finally:
        conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="yadokari")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("db", help="DB の操作")
    p.add_argument("action", choices=["migrate"])
    p.set_defaults(func=_cmd_db)

    p = sub.add_parser("collect", help="ソースから記事を集める")
    p.add_argument("--source", action="append", help="ソース名（複数可）。省略時は全部")
    p.add_argument("--limit", type=int, default=None, help="1ソースあたりの上限")
    p.add_argument("--dry-run", action="store_true", help="DB に書かずに何が入るかだけ見る")
    p.set_defaults(func=_cmd_collect)

    p = sub.add_parser("score", help="未採点の記事を採点する")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=_cmd_score)

    p = sub.add_parser("list", help="記事の一覧")
    p.add_argument("--status")
    p.add_argument("--min-score", type=float)
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("stats", help="ソース別・状態別の件数")
    p.set_defaults(func=_cmd_stats)

    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.app.log_dir, cfg.app.log_level)
    return args.func(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
