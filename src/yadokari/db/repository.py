"""articles / feedback の読み書き。SQL は SQLite の書き方（? プレースホルダ）で書く。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from yadokari.db.connection import DbConnection, Row


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class NewArticle:
    source: str
    source_url: str
    title: str | None = None
    published_at: str | None = None
    content_text: str | None = None
    image_urls: list[str] = field(default_factory=list)
    og_image: str | None = None
    photo_credit: str | None = None


def exists_source_url(conn: DbConnection, url: str) -> bool:
    row = conn.execute("SELECT 1 FROM articles WHERE source_url = ?", (url,)).fetchone()
    return row is not None


def insert_article(conn: DbConnection, a: NewArticle) -> int | None:
    """入れた行の id を返す。既にあれば None（**再実行しても重複しない**）。"""
    row = conn.execute(
        "INSERT INTO articles (source, source_url, title, published_at, collected_at,"
        " content_text, image_urls, image_count, og_image, photo_credit)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (source_url) DO NOTHING RETURNING id",
        (
            a.source, a.source_url, a.title, a.published_at, now_iso(), a.content_text,
            json.dumps(a.image_urls, ensure_ascii=False), len(a.image_urls), a.og_image,
            a.photo_credit,
        ),
    ).fetchone()
    return row["id"] if row else None


def unscored(conn: DbConnection, limit: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM articles WHERE status = 'collected' ORDER BY collected_at LIMIT ?",
        (limit,),
    ).fetchall()


def save_score(
    conn: DbConnection, article_id: int, score: float, detail: dict, assessment: dict, model: str
) -> None:
    conn.execute(
        "UPDATE articles SET status = 'scored', score = ?, score_detail = ?, assessment = ?,"
        " scored_at = ?, scored_model = ?, score_error = NULL"
        " WHERE id = ? AND status = 'collected'",
        (
            score, json.dumps(detail, ensure_ascii=False),
            json.dumps(assessment, ensure_ascii=False), now_iso(), model, article_id,
        ),
    )


def save_score_error(conn: DbConnection, article_id: int, error: str) -> None:
    """失敗は行に残す。status は collected のままなので次回また採点に回る。"""
    conn.execute("UPDATE articles SET score_error = ? WHERE id = ?", (error[:1000], article_id))


def list_articles(
    conn: DbConnection, status: str | None = None, min_score: float | None = None, limit: int = 50,
    duplicates: bool | None = None,
) -> list[Row]:
    """duplicates: None=区別しない / False=重複を除く / True=重複だけ（duplicate_of が 0 は「重複ではない」）"""
    sql = "SELECT * FROM articles WHERE 1=1"
    params: list = []
    if duplicates is False:
        sql += " AND (duplicate_of IS NULL OR duplicate_of = 0)"
    elif duplicates is True:
        sql += " AND duplicate_of > 0"
    if status:
        sql += " AND status = ?"
        params.append(status)
    if min_score is not None:
        sql += " AND score >= ?"
        params.append(min_score)
    sql += " ORDER BY score DESC NULLS LAST, collected_at DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, tuple(params)).fetchall()


def counts_by_source(conn: DbConnection) -> list[Row]:
    return conn.execute(
        "SELECT source, status, COUNT(*) AS n FROM articles GROUP BY source, status"
        " ORDER BY source, status"
    ).fetchall()


def get_article(conn: DbConnection, article_id: int) -> Row | None:
    return conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()


# ----------------------------------------------------------------------
# 審査
# ----------------------------------------------------------------------
DECISIONS = ("approved", "rejected")


def decide(conn: DbConnection, article_id: int, decision: str, reason: str | None = None,
           tag: str | None = None) -> None:
    """承認・非承認。非承認の理由は feedback に残し、学習ループに回す。

    タグを人が選んだときはそれを使い、LLM の分類は飛ばす。
    下書きを WP に送ったあとの記事は状態を戻さない（WP 側と食い違うため）。
    """
    if decision not in DECISIONS:
        raise ValueError(decision)
    pushed = conn.execute(
        "SELECT 1 FROM drafts WHERE article_id = ? AND wp_post_id IS NOT NULL", (article_id,)
    ).fetchone()
    if pushed:
        raise ValueError("WordPress に送った記事は審査をやり直せません（WP 側で扱ってください）")
    conn.execute("UPDATE articles SET status = ? WHERE id = ?", (decision, article_id))
    conn.execute(
        "INSERT INTO feedback (article_id, decision, reason, tag, created_at) VALUES (?, ?, ?, ?, ?)",
        (article_id, decision, (reason or "").strip() or None, tag or None, now_iso()),
    )


def reset_to_scored(conn: DbConnection, article_id: int) -> None:
    """審査を取り消す（押し間違い用）。送信前の下書きは消す。"""
    pushed = conn.execute(
        "SELECT 1 FROM drafts WHERE article_id = ? AND wp_post_id IS NOT NULL", (article_id,)
    ).fetchone()
    if pushed:
        raise ValueError("WordPress に送った記事は戻せません")
    conn.execute("DELETE FROM drafts WHERE article_id = ?", (article_id,))
    conn.execute(
        "UPDATE articles SET status = CASE WHEN score IS NULL THEN 'collected' ELSE 'scored' END"
        " WHERE id = ?",
        (article_id,),
    )


# ----------------------------------------------------------------------
# 下書き
# ----------------------------------------------------------------------
def save_draft(conn: DbConnection, article_id: int, *, title: str, excerpt: str, body_html: str,
               tags: list[str], featured_image: str | None, warnings: list[str],
               model: str) -> int:
    """作り直しても1記事1本。WP の post ID は消さない（次の送信で同じ投稿を更新する）。"""
    existing = get_draft_by_article(conn, article_id)
    values = (title, excerpt, body_html, json.dumps(tags, ensure_ascii=False), featured_image,
              json.dumps(warnings, ensure_ascii=False), model, now_iso())
    if existing is None:
        row = conn.execute(
            "INSERT INTO drafts (title, excerpt, body_html, tags, featured_image, warnings, model,"
            " generated_at, article_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (*values, article_id),
        ).fetchone()
        return row["id"]
    conn.execute(
        "UPDATE drafts SET title = ?, excerpt = ?, body_html = ?, tags = ?, featured_image = ?,"
        " warnings = ?, model = ?, generated_at = ?, edited_at = NULL WHERE id = ?",
        (*values, existing["id"]),
    )
    return existing["id"]


def get_draft(conn: DbConnection, draft_id: int) -> Row | None:
    return conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()


def get_draft_by_article(conn: DbConnection, article_id: int) -> Row | None:
    return conn.execute("SELECT * FROM drafts WHERE article_id = ?", (article_id,)).fetchone()


def edit_draft(conn: DbConnection, draft_id: int, *, title: str, excerpt: str, body_html: str,
               tags: list[str], featured_image: str | None, scheduled_at: str | None) -> None:
    """人の編集。公開済みのものは触らない。"""
    conn.execute(
        "UPDATE drafts SET title = ?, excerpt = ?, body_html = ?, tags = ?, featured_image = ?,"
        " scheduled_at = ?, edited_at = ? WHERE id = ? AND state != 'published'",
        (title, excerpt, body_html, json.dumps(tags, ensure_ascii=False), featured_image,
         scheduled_at, now_iso(), draft_id),
    )


def list_drafts(conn: DbConnection, state: str | None = None) -> list[Row]:
    sql = ("SELECT d.*, a.source, a.source_url, a.score FROM drafts d"
           " JOIN articles a ON a.id = d.article_id")
    params: tuple = ()
    if state:
        sql += " WHERE d.state = ?"
        params = (state,)
    sql += " ORDER BY COALESCE(d.scheduled_at, d.generated_at) DESC"
    return conn.execute(sql, params).fetchall()


def taken_slots(conn: DbConnection, since: str) -> set[str]:
    rows = conn.execute(
        "SELECT scheduled_at FROM drafts WHERE scheduled_at IS NOT NULL AND scheduled_at >= ?",
        (since,),
    ).fetchall()
    return {r["scheduled_at"] for r in rows}


def mark_pushed(conn: DbConnection, draft_id: int, *, wp_post_id: int, wp_status: str,
                wp_link: str | None, state: str, media_id: int | None) -> None:
    conn.execute(
        "UPDATE drafts SET wp_post_id = ?, wp_status = ?, wp_link = ?, state = ?,"
        " wp_media_id = COALESCE(?, wp_media_id), pushed_at = ?, error = NULL WHERE id = ?",
        (wp_post_id, wp_status, wp_link, state, media_id, now_iso(), draft_id),
    )


def mark_error(conn: DbConnection, draft_id: int, error: str) -> None:
    conn.execute("UPDATE drafts SET error = ? WHERE id = ?", (error[:1000], draft_id))


def mark_published(conn: DbConnection, draft_id: int, link: str | None, published_at: str) -> None:
    conn.execute(
        "UPDATE drafts SET state = 'published', wp_status = 'publish',"
        " wp_link = COALESCE(?, wp_link), published_at = ? WHERE id = ?",
        (link, published_at, draft_id),
    )


def drafts_to_sync(conn: DbConnection) -> list[Row]:
    return conn.execute(
        "SELECT * FROM drafts WHERE wp_post_id IS NOT NULL AND state IN ('wp_draft', 'scheduled')"
    ).fetchall()


# ----------------------------------------------------------------------
# 学習ループ
# ----------------------------------------------------------------------
def untagged_feedback(conn: DbConnection, limit: int = 20) -> list[Row]:
    return conn.execute(
        "SELECT id, reason FROM feedback WHERE decision = 'rejected' AND tag IS NULL"
        " AND reason IS NOT NULL ORDER BY id LIMIT ?",
        (limit,),
    ).fetchall()


def set_feedback_tag(conn: DbConnection, feedback_id: int, tag: str) -> None:
    conn.execute("UPDATE feedback SET tag = ? WHERE id = ?", (tag, feedback_id))


def tag_counts(conn: DbConnection) -> list[Row]:
    return conn.execute(
        "SELECT tag, COUNT(*) AS hits FROM feedback WHERE decision = 'rejected'"
        " AND tag IS NOT NULL AND tag != 'other' GROUP BY tag ORDER BY hits DESC"
    ).fetchall()


def reasons_for_tag(conn: DbConnection, tag: str, limit: int = 10) -> list[str]:
    rows = conn.execute(
        "SELECT f.reason, a.title FROM feedback f JOIN articles a ON a.id = f.article_id"
        " WHERE f.tag = ? AND f.decision = 'rejected' ORDER BY f.id DESC LIMIT ?",
        (tag, limit),
    ).fetchall()
    return [f"{r['title'] or ''}: {r['reason'] or '（理由なし）'}" for r in rows]


def upsert_rule_candidate(conn: DbConnection, tag: str, hits: int, proposal: str) -> bool:
    """新規に提案したときだけ True。**人が dismissed にした候補は蒸し返さない。**"""
    existing = conn.execute(
        "SELECT state FROM rule_candidates WHERE reason_tag = ?", (tag,)
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO rule_candidates (reason_tag, hit_count, proposal, state, created_at)"
            " VALUES (?, ?, ?, 'proposed', ?)",
            (tag, hits, proposal, now_iso()),
        )
        return True
    if existing["state"] == "proposed":
        conn.execute(
            "UPDATE rule_candidates SET hit_count = ?, proposal = ?, updated_at = ?"
            " WHERE reason_tag = ?",
            (hits, proposal, now_iso(), tag),
        )
    else:
        conn.execute(
            "UPDATE rule_candidates SET hit_count = ?, updated_at = ? WHERE reason_tag = ?",
            (hits, now_iso(), tag),
        )
    return False


def list_rule_candidates(conn: DbConnection) -> list[Row]:
    return conn.execute(
        "SELECT * FROM rule_candidates ORDER BY CASE state WHEN 'proposed' THEN 0"
        " WHEN 'approved' THEN 1 ELSE 2 END, hit_count DESC"
    ).fetchall()


def set_rule_state(conn: DbConnection, rule_id: int, state: str, proposal: str | None = None) -> None:
    if state not in ("approved", "dismissed", "proposed"):
        raise ValueError(state)
    conn.execute(
        "UPDATE rule_candidates SET state = ?, proposal = COALESCE(?, proposal), updated_at = ?"
        " WHERE id = ?",
        (state, proposal, now_iso(), rule_id),
    )


def approved_rules(conn: DbConnection) -> list[str]:
    """人が承認したルールだけ。採点のプロンプトに載せる。"""
    rows = conn.execute(
        "SELECT proposal FROM rule_candidates WHERE state = 'approved' ORDER BY hit_count DESC"
    ).fetchall()
    return [r["proposal"] for r in rows if r["proposal"]]
