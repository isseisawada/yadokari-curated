"""媒体をまたいだ重複（2026-09-29: Wiki World の Red Submarine Cabin が ArchDaily と designboom から入った）。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from helpers import add_article, assessment

from yadokari.db.connection import connect
from yadokari.db.migrate import migrate
from yadokari.scoring.dedupe import mark_duplicates
from yadokari.web.app import create_app


def _set(conn, aid, title, **facts):
    a = assessment()
    a["facts"] = {**a["facts"], "builder": "", "architect": "", "name": ""} | facts
    conn.execute("UPDATE articles SET title = ?, assessment = ? WHERE id = ?",
                 (title, json.dumps(a, ensure_ascii=False), aid))
    conn.commit()


def test_same_work_from_another_media_is_marked(db):
    first = add_article(db, url="https://www.archdaily.com/1/red-submarine")
    later = add_article(db, url="https://www.designboom.com/red-submarine")
    _set(db, first, "Red Submarine Cabin / Wiki World", name="Red Submarine Cabin", architect="Wiki World")
    _set(db, later, "compact forest cabin by wiki world takes shape as a red wooden submarine",
         name="Red Submarine Cabin（Wiki World-Red Submarine Cabin-[Wild Cabin #140]）", architect="Wiki World")
    assert mark_duplicates(db) == 1
    rows = {r["id"]: r["duplicate_of"] for r in db.execute("SELECT id, duplicate_of FROM articles")}
    assert rows == {first: None, later: first}
    assert mark_duplicates(db) == 0  # 何度走らせても同じ


def test_same_designer_title_overlap_without_name(db):
    a = add_article(db, url="https://t.com/a")
    b = add_article(db, url="https://t.com/b")
    _set(db, a, "Koto Niwa Cabin / Koto", architect="Koto")
    _set(db, b, "Koto unveils the Niwa cabin", architect="KOTO")
    assert mark_duplicates(db) == 1


def test_different_works_are_not_marked(db):
    a = add_article(db, url="https://t.com/a")
    b = add_article(db, url="https://t.com/b")
    c = add_article(db, url="https://t.com/c")
    # 同じビルダーの別モデル
    _set(db, a, "Single-level tiny house makes smart use of limited space", builder="Tiny Heirloom",
         name="Aspen")
    _set(db, b, "Single-level tiny house creates spacious cottage living on wheels", builder="Tiny Heirloom",
         name="Cottage 24")
    # 名前は似ているが設計者が違う
    _set(db, c, "Aspen tiny house by another builder", builder="Other Co", name="Aspen")
    assert mark_duplicates(db) == 0


def test_decided_articles_are_left_alone(db):
    first = add_article(db, url="https://t.com/a")
    later = add_article(db, url="https://t.com/b", status="rejected")
    for aid in (first, later):
        _set(db, aid, "Red Submarine Cabin / Wiki World", name="Red Submarine Cabin", architect="Wiki World")
    assert mark_duplicates(db) == 0


def test_duplicates_leave_the_review_queue_and_can_be_restored(config, tmp_path):
    path = tmp_path / "w.db"
    migrate(path)
    config.app.db_path = str(path)
    conn = connect(path)
    c = TestClient(create_app(config))
    first = add_article(conn, url="https://t.com/a")
    later = add_article(conn, url="https://t.com/b")
    for aid in (first, later):
        _set(conn, aid, "Red Submarine Cabin / Wiki World", name="Red Submarine Cabin", architect="Wiki World")
    mark_duplicates(conn)
    conn.commit()
    assert c.get("/articles").text.count('class="card"') == 1
    assert "重複 1" in c.get("/articles").text
    assert f"#{first} と同じ作品" in c.get("/articles?status=duplicate").text
    assert "別の媒体で同じ作品" in c.get(f"/articles/{later}").text
    c.post(f"/articles/{later}/not-duplicate")
    assert c.get("/articles").text.count('class="card"') == 2
    mark_duplicates(conn)  # 人が「重複ではない」にしたものは付け直さない
    assert conn.execute("SELECT duplicate_of FROM articles WHERE id = ?", (later,)).fetchone()[0] == 0
    conn.close()
