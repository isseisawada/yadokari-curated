"""承認した記事を、下書きにして空いている最短の枠へ予約する（2026-09-30 ユーザー指定）。

毎時の GitHub Actions（auto.yml）から呼ぶ。1回に作る下書きは limit 本まで（課金を分散する）。

1. 下書きがあって、まだ予約していないもの（承認済みの記事）→ 空いている最短の枠で予約
2. 承認済みで下書きが無い記事（承認の古い順）→ 下書きを作って、同じく予約

**元記事に無い数字が残った下書きは予約しない**（人が直す。審査画面で目立つ）。
送信に失敗したら、その枠は空けておく（次の回か人が送る）。
止めるときは config.yaml の wordpress.auto_schedule を false にする。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from yadokari.config import Config
from yadokari.db.connection import DbConnection
from yadokari.db.repository import get_draft, set_schedule
from yadokari.logging_setup import get_logger

log = get_logger(__name__)

NUMBER_WARNING = "元資料に無い数字"


@dataclass
class AutoResult:
    scheduled: list[tuple[int, str]] = field(default_factory=list)  # (draft_id, 予定 UTC)
    generated: list[int] = field(default_factory=list)
    held: list[tuple[int, str]] = field(default_factory=list)  # (draft_id, 理由)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (対象, エラー)


def _unscheduled_drafts(conn: DbConnection, now: datetime) -> list[int]:
    soon = (now + timedelta(minutes=30)).isoformat()
    rows = conn.execute(
        "SELECT d.id FROM drafts d JOIN articles a ON a.id = d.article_id"
        " WHERE a.status = 'approved' AND d.state IN ('editing', 'wp_draft')"
        " AND (d.scheduled_at IS NULL OR d.scheduled_at < ?)"
        " ORDER BY d.id",
        (soon,),
    ).fetchall()
    return [r["id"] for r in rows]


def _approved_without_draft(conn: DbConnection) -> list[int]:
    rows = conn.execute(
        "SELECT a.id, MAX(f.created_at) AS approved_at FROM articles a"
        " LEFT JOIN feedback f ON f.article_id = a.id AND f.decision = 'approved'"
        " WHERE a.status = 'approved'"
        " AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.article_id = a.id)"
        " GROUP BY a.id ORDER BY approved_at, a.id",
    ).fetchall()
    return [r["id"] for r in rows]


def _schedule(config: Config, conn: DbConnection, draft_id: int, res: AutoResult, *,
              wp, fetch, now: datetime) -> None:
    from yadokari.wordpress.publish import push, suggest_slot

    draft = get_draft(conn, draft_id)
    stray = [w for w in json.loads(draft["warnings"] or "[]") if w.startswith(NUMBER_WARNING)]
    if stray:
        res.held.append((draft_id, "、".join(stray)))
        return
    when = suggest_slot(config, conn, now).isoformat()
    set_schedule(conn, draft_id, when)
    conn.commit()
    try:
        push(config, conn, draft_id, schedule=True, wp=wp, fetch=fetch)
    except Exception as exc:  # noqa: BLE001 - 1本の失敗で残りを止めない
        set_schedule(conn, draft_id, None)
        conn.commit()
        res.failed.append((f"draft {draft_id}", str(exc)))
        log.warning("予約に失敗しました: draft=%s %s", draft_id, exc)
        return
    res.scheduled.append((draft_id, when))
    log.info("予約しました: draft=%s %s", draft_id, when)


def run(config: Config, conn: DbConnection, *, limit: int = 3, client=None, wp=None,
        fetch=None, now: datetime | None = None) -> AutoResult:
    from yadokari.drafting.generate import generate_for
    from yadokari.wordpress.client import WordPressClient

    res = AutoResult()
    if not (config.wordpress.allow_schedule and config.wordpress.auto_schedule):
        return res
    now = now or datetime.now(UTC)
    own = wp is None
    wp = wp or WordPressClient(config.wordpress)
    try:
        for draft_id in _unscheduled_drafts(conn, now):
            _schedule(config, conn, draft_id, res, wp=wp, fetch=fetch, now=now)
        for article_id in _approved_without_draft(conn)[:limit]:
            try:
                draft_id = generate_for(config, conn, article_id, client=client)
            except Exception as exc:  # noqa: BLE001 - 1本の失敗で残りを止めない
                conn.rollback()
                res.failed.append((f"article {article_id}", str(exc)))
                log.warning("下書きに失敗しました: article=%s %s", article_id, exc)
                continue
            res.generated.append(draft_id)
            _schedule(config, conn, draft_id, res, wp=wp, fetch=fetch, now=now)
    finally:
        if own:
            wp.close()
    return res
