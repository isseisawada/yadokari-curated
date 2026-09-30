"""コマンド。

    python -m yadokari.cli db migrate
    python -m yadokari.cli collect [--source archdaily] [--limit 5] [--dry-run]
    python -m yadokari.cli score [--limit 40]
    python -m yadokari.cli hero [--limit 60]
    python -m yadokari.cli dedupe
    python -m yadokari.cli list [--status scored] [--min-score 50]
    python -m yadokari.cli stats
    python -m yadokari.cli serve [--host 127.0.0.1] [--port 8000]
    python -m yadokari.cli draft ARTICLE_ID
    python -m yadokari.cli wp push DRAFT_ID [--schedule]
    python -m yadokari.cli wp sync
    python -m yadokari.cli wp check
    python -m yadokari.cli wp images
    python -m yadokari.cli wp resend
    python -m yadokari.cli auto [--limit 3]
    python -m yadokari.cli x post DRAFT_ID [--dry-run]
    python -m yadokari.cli learn
    python -m yadokari.cli monitor
    python -m yadokari.cli report
    python -m yadokari.cli validate import validation/positives.tsv --label pos [--limit N]
    python -m yadokari.cli validate score
    python -m yadokari.cli validate report
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


def _cmd_hero(cfg, args) -> int:
    from yadokari.scoring.hero import pick_pending

    ensure_migrated(cfg.app.target())
    conn = connect(cfg.app.target())
    try:
        picked, failed = pick_pending(cfg, conn, limit=args.limit)
    finally:
        conn.close()
    # 画像を取れずに選べなかったものは元の並びのまま使えるので、失敗でもジョブは止めない
    print(f"1枚目（外観）を選んだ {picked} 件（選べなかった {failed} 件）")
    return 0


def _cmd_dedupe(cfg, args) -> int:
    from yadokari.scoring.dedupe import mark_duplicates

    ensure_migrated(cfg.app.target())
    conn = connect(cfg.app.target())
    try:
        n = mark_duplicates(conn)
        conn.commit()
    finally:
        conn.close()
    print(f"重複（別の媒体で同じ作品）に印を付けた {n} 件")
    return 0


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


def _cmd_serve(cfg, args) -> int:
    import uvicorn

    from yadokari.web.app import create_app
    from yadokari.web.auth import require_credentials

    auth = require_credentials(args.host)
    ensure_migrated(cfg.app.target())
    uvicorn.run(create_app(cfg, auth=auth), host=args.host, port=args.port)
    return 0


def _cmd_draft(cfg, args) -> int:
    from yadokari.drafting.generate import generate_for

    ensure_migrated(cfg.app.target())
    conn = connect(cfg.app.target())
    try:
        draft_id = generate_for(cfg, conn, args.article_id)
        row = conn.execute("SELECT title, warnings FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    finally:
        conn.close()
    print(f"下書き {draft_id}: {row['title']}")
    for w in json.loads(row["warnings"] or "[]"):
        print("  要確認:", w)
    return 0


def _wp_check(cfg) -> int:
    """WordPress に**読み取りだけ**でつないで、投稿に要る権限とカテゴリ・タグを確かめる。何も書かない。"""
    from yadokari.wordpress.client import WordPressClient, WordPressError

    ok = True
    # 値は出さずに形だけ見る（アプリケーションパスワードは空白を除いて英数字24文字）
    user, password = cfg.wordpress.require_credentials()
    core = "".join(ch for ch in password if ch.isalnum())
    print(f"WP_USER: {len(user)}文字{'（メールアドレス形式）' if '@' in user else ''}"
          f"{' 前後に空白あり' if user != user.strip() else ''}")
    quoted = any(q in password for q in ('"', "'"))
    print(f"WP_APP_PASSWORD: 英数字{len(core)}文字（24文字のはず）"
          f"{' 前後に空白・改行あり' if password != password.strip() else ''}"
          f"{' 引用符あり' if quoted else ''}")
    try:
        with WordPressClient(cfg.wordpress) as wp:
            me = wp.me()
            caps = me.get("capabilities") or {}
            print(f"ログイン: {me.get('name')}（{me.get('slug')}） 権限: {', '.join(me.get('roles') or [])}")
            for cap, label in (("edit_posts", "下書きを作る"), ("publish_posts", "予約・公開する"),
                               ("upload_files", "アイキャッチを取り込む")):
                has = bool(caps.get(cap))
                ok &= has
                print(f"  {label}: {'OK' if has else 'できない'}")
            for cid in cfg.wordpress.category_ids:
                print(f"  カテゴリ {cid}: {wp.get_term('categories', cid).get('name')}")
            for name, tid in cfg.seo.tag_ids.items():
                got = wp.get_term("tags", tid).get("name")
                ok &= got == name
                print(f"  タグ {tid}: {got}{'' if got == name else f'（{name} のはず）'}")
    except WordPressError as exc:
        print(f"つながりません: {exc}")
        return 1
    return 0 if ok else 1


def _cmd_wp(cfg, args) -> int:
    from yadokari.wordpress.publish import push, refresh_images, resend, sync

    if args.action == "check":
        return _wp_check(cfg)

    ensure_migrated(cfg.app.target())
    conn = connect(cfg.app.target())
    try:
        if args.action == "push":
            if args.draft_id is None:
                print("DRAFT_ID を指定してください")
                return 2
            r = push(cfg, conn, args.draft_id, schedule=args.schedule)
            print(f"post {r.post_id} status={r.status} {r.link or ''}")
            for n in r.notes:
                print("  ", n)
            return 0
        if args.action == "resend":
            done = resend(cfg, conn)
            for draft_id, what in done:
                print(f"draft {draft_id}: {what}")
            print(f"送り直し {len(done)} 本")
            return 1 if any("失敗" in w for _, w in done) else 0
        if args.action == "images":
            done = refresh_images(cfg, conn)
            for draft_id, what in done:
                print(f"draft {draft_id}: {what}")
            print(f"写真を差し替え {len(done)} 本")
            return 1 if any("失敗" in w for _, w in done) else 0
        res = sync(cfg, conn)
    finally:
        conn.close()
    print(f"確認 {res.checked} 件 / 公開 {len(res.published)} 件")
    for draft_id, link in res.published:
        print(f"  公開: draft {draft_id} {link or ''}")
    for draft_id, when in res.missed:
        print(f"  要確認: draft {draft_id} は予定 {when} を過ぎても公開されていません（予約投稿の失敗）")
    return 1 if res.missed else 0


def _cmd_auto(cfg, args) -> int:
    from yadokari import autopilot

    ensure_migrated(cfg.app.target())
    if not (cfg.wordpress.allow_schedule and cfg.wordpress.auto_schedule):
        print("自動予約は止めてあります（config.yaml の wordpress.auto_schedule / allow_schedule）")
        return 0
    conn = connect(cfg.app.target())
    try:
        res = autopilot.run(cfg, conn, limit=args.limit)
    finally:
        conn.close()
    print(f"下書き {len(res.generated)} 本 / 予約 {len(res.scheduled)} 本"
          f" / 保留 {len(res.held)} 本 / 失敗 {len(res.failed)} 件")
    for draft_id, when in res.scheduled:
        print(f"  予約: draft {draft_id} {when}")
    for draft_id, why in res.held:
        print(f"  保留（人が直す）: draft {draft_id} {why}")
    for what, err in res.failed:
        print(f"  失敗: {what} {err}")
    return 1 if res.failed else 0


def _cmd_x(cfg, args) -> int:
    """公開済みの記事を、こちらから X に投稿する（例外用。ふだんは WP が公開の瞬間に投稿）。"""
    from yadokari.db.repository import get_draft
    from yadokari.sns.x import compose, keys_from_env, post
    from yadokari.wordpress.client import WordPressClient

    conn = connect(cfg.app.target())
    try:
        d = get_draft(conn, args.draft_id)
    finally:
        conn.close()
    if d is None or d["wp_post_id"] is None:
        print("WP に送った下書きではありません")
        return 2
    with WordPressClient(cfg.wordpress) as wp:
        wp_post = wp.get_post(d["wp_post_id"])
    if wp_post.status != "publish":
        print(f"まだ公開されていません（{wp_post.status}）。公開前の記事は WP が公開の瞬間に投稿します")
        return 2
    text = compose(cfg.x, title=d["title"], prefix=cfg.drafting.title_prefix,
                   quote=d["quote"] or "", tags=json.loads(d["tags"] or "[]"))
    text += "\n\n" + (wp_post.link or "")
    print(text)
    if args.dry_run:
        return 0
    tweet_id = post(text, keys_from_env())
    print(f"投稿しました: https://x.com/i/web/status/{tweet_id}")
    return 0


def _cmd_learn(cfg, args) -> int:
    from yadokari.learning.loop import run_learning

    ensure_migrated(cfg.app.target())
    conn = connect(cfg.app.target())
    try:
        stats = run_learning(cfg, conn)
    finally:
        conn.close()
    print(stats.summary())
    for line in stats.new_candidates:
        print("  提案:", line)
    return 0


def _cmd_monitor(cfg, args) -> int:
    from yadokari.monitor import check

    conn = None
    try:
        conn = connect(cfg.app.target())
    except Exception as exc:  # noqa: BLE001 - DB が落ちていても WP 側は確かめる
        print(f"要確認: DB に繋がりません: {exc}")
    try:
        report = check(cfg, conn)
    finally:
        if conn is not None:
            conn.close()
    print("\n".join(report.lines))
    print("判定:", "OK" if report.ok else "要確認")
    return 0 if report.ok else 1


def _cmd_report(cfg, args) -> int:
    from yadokari.report import weekly

    conn = connect(cfg.app.target())
    try:
        print(weekly(conn, days=args.days))
    finally:
        conn.close()
    return 0


def _cmd_validate(cfg, args) -> int:
    from yadokari import validation

    ensure_migrated(cfg.app.target())
    conn = connect(cfg.app.target())
    try:
        if args.action == "import":
            if not args.file:
                print("取り込むファイルを指定してください")
                return 2
            st = validation.import_list(cfg, conn, args.file, args.label, limit=args.limit)
            print(f"取り込み {st.added} 件 / 既にある {st.existing} 件 / 取得失敗 {len(st.failed)} 件")
            for f in st.failed:
                print("  失敗:", f)
        elif args.action == "score":
            ok, ng = validation.score_all(cfg, conn, limit=args.limit or 200)
            print(f"採点 {ok} 件（失敗 {ng}）")
        else:
            print(validation.report(cfg, conn))
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

    p = sub.add_parser("hero", help="審査ライン以上の記事で、1枚目に使う外観の写真を選ぶ")
    p.add_argument("--limit", type=int, default=60)
    p.set_defaults(func=_cmd_hero)

    p = sub.add_parser("dedupe", help="別の媒体で同じ作品を紹介している記事に印を付ける")
    p.set_defaults(func=_cmd_dedupe)

    p = sub.add_parser("list", help="記事の一覧")
    p.add_argument("--status")
    p.add_argument("--min-score", type=float)
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("stats", help="ソース別・状態別の件数")
    p.set_defaults(func=_cmd_stats)

    p = sub.add_parser("serve", help="審査画面を起動する")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=_cmd_serve)

    p = sub.add_parser("draft", help="承認済みの記事から下書きを作る")
    p.add_argument("article_id", type=int)
    p.set_defaults(func=_cmd_draft)

    p = sub.add_parser("wp", help="WordPress への送信と状態の確認")
    p.add_argument("action", choices=["check", "push", "sync", "images", "resend"])
    p.add_argument("draft_id", type=int, nargs="?")
    p.add_argument("--schedule", action="store_true", help="予約投稿にする（allow_schedule が要る）")
    p.set_defaults(func=_cmd_wp)

    p = sub.add_parser("auto", help="承認済みを下書きにして、空いている最短の枠へ予約する")
    p.add_argument("--limit", type=int, default=3, help="1回に作る下書きの上限")
    p.set_defaults(func=_cmd_auto)

    p = sub.add_parser("x", help="公開済みの記事を X に投稿する（例外用）")
    p.add_argument("action", choices=["post"])
    p.add_argument("draft_id", type=int)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=_cmd_x)

    p = sub.add_parser("learn", help="非承認理由からルール候補を育てる")
    p.set_defaults(func=_cmd_learn)

    p = sub.add_parser("monitor", help="昨日・今日の分が出たかを確かめる")
    p.set_defaults(func=_cmd_monitor)

    p = sub.add_parser("report", help="週次レポート")
    p.add_argument("--days", type=int, default=7)
    p.set_defaults(func=_cmd_report)

    p = sub.add_parser("validate", help="採点の検証（載せたい／載せたくない見本）")
    p.add_argument("action", choices=["import", "score", "report"])
    p.add_argument("file", nargs="?")
    p.add_argument("--label", choices=["pos", "neg"], default="pos")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=_cmd_validate)

    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg.app.log_dir, cfg.app.log_level)
    return args.func(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
