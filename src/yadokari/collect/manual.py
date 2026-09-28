"""手動投入。利用規約で自動収集を禁じているサイト（ArchDaily・Dwell・Gessato）用。

**ページは取得しない。** 人がブラウザで読んだ記事の URL・タイトル・本文・写真URLを入れる。
frmg の collect/manual.py と同じ建て付け（取得しないので robots も規約も踏まない）。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from yadokari.collect.page import normalize_url
from yadokari.config import Config
from yadokari.db.connection import DbConnection
from yadokari.db.repository import NewArticle, insert_article


@dataclass
class ManualResult:
    article_id: int | None
    duplicate: bool


def add_manual(config: Config, conn: DbConnection, *, source: str, url: str, title: str,
               text: str, image_urls: list[str], credit: str | None = None,
               published_at: str | None = None) -> ManualResult:
    src = config.source(source)
    if not src.manual_only:
        raise ValueError(f"{source} は自動収集のソースです。手動で入れるのは manual_only のソースだけ")
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("URL が正しくありません")
    if not title.strip() or not text.strip():
        raise ValueError("タイトルと本文は必須です（採点と下書きの材料になります）")
    images = []
    for line in image_urls:
        u = line.strip()
        if u.startswith(("http://", "https://")) and u not in images:
            images.append(u)
    article_id = insert_article(conn, NewArticle(
        source=source, source_url=normalize_url(url), title=title.strip(),
        published_at=published_at, content_text=" ".join(text.split()),
        image_urls=images, og_image=images[0] if images else None,
        photo_credit=(credit or "").strip() or None,
    ))
    conn.commit()
    return ManualResult(article_id, article_id is None)
