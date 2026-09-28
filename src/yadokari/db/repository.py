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
    conn: DbConnection, status: str | None = None, min_score: float | None = None, limit: int = 50
) -> list[Row]:
    sql = "SELECT * FROM articles WHERE 1=1"
    params: list = []
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
