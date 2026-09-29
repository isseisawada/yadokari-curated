"""審査画面の追加分（2026-09-29 ユーザー要望）: 媒体ラベルの色・一覧からの承認/非承認・1枚目を外観に。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from helpers import FakeLLM, add_article

from yadokari.db.connection import connect
from yadokari.db.migrate import migrate
from yadokari.scoring.hero import ordered_images, pick_pending
from yadokari.web.app import create_app


@pytest.fixture
def env(config, tmp_path):
    path = tmp_path / "w.db"
    migrate(path)
    config.app.db_path = str(path)
    conn = connect(path)
    yield TestClient(create_app(config)), conn
    conn.close()


def test_every_source_has_a_label_color_not_the_decision_colors(config):
    for s in config.sources:
        assert s.color, s.name
        # 承認の青・非承認の赤と見分けがつくように
        assert s.color.upper() not in ("#003AF2", "#EB4E42"), s.name


def test_label_uses_source_color(env):
    c, conn = env
    aid = add_article(conn)
    conn.execute("UPDATE articles SET source = 'newatlas' WHERE id = ?", (aid,))
    conn.commit()
    assert "background:#0086A8;color:#fff" in c.get("/articles").text
    assert "background:#0086A8" in c.get(f"/articles/{aid}").text


def test_approve_from_list_returns_to_the_list(env):
    c, conn = env
    aid = add_article(conn)
    r = c.post(f"/articles/{aid}/decide", data={"decision": "approved", "next": "/articles?status=scored"},
               follow_redirects=False)
    assert r.headers["location"].startswith("/articles?status=scored&msg=")
    assert conn.execute("SELECT status FROM articles WHERE id = ?", (aid,)).fetchone()["status"] == "approved"


def test_list_decide_by_fetch_returns_json(env):
    c, conn = env
    aid = add_article(conn)
    h = {"X-Requested-With": "fetch"}
    r = c.post(f"/articles/{aid}/decide", data={"decision": "rejected", "next": "/articles"}, headers=h)
    assert r.status_code == 400 and not r.json()["ok"]  # 理由が無い非承認は受け付けない
    r = c.post(f"/articles/{aid}/decide",
               data={"decision": "rejected", "tag": "デザインが弱い", "next": "/articles"}, headers=h)
    assert r.json() == {"ok": True, "message": "非承認しました"}
    assert conn.execute("SELECT status FROM articles WHERE id = ?", (aid,)).fetchone()["status"] == "rejected"


def test_next_cannot_redirect_off_site(env):
    c, conn = env
    aid = add_article(conn)
    r = c.post(f"/articles/{aid}/decide", data={"decision": "approved", "next": "//evil.example.com"},
               follow_redirects=False)
    assert r.headers["location"].startswith(f"/articles/{aid}")


def test_list_has_quick_buttons_only_for_unreviewed(env):
    c, conn = env
    add_article(conn, url="https://t.com/a")
    html = c.get("/articles").text
    assert "class=\"js-decide\"" in html and "非承認にする" in html
    assert "class=\"js-decide\"" not in c.get("/articles?status=approved").text


def _hero_llm(index: int) -> FakeLLM:
    return FakeLLM({"index": index, "reason": "外観が全体まで写っている"})


def test_pick_exterior_moves_it_to_the_front(config, db):
    aid = add_article(db)
    llm = _hero_llm(3)
    picked, failed = pick_pending(config, db, client=llm)
    assert (picked, failed) == (1, 0)
    row = db.execute("SELECT * FROM articles WHERE id = ?", (aid,)).fetchone()
    assert row["hero_image"] == "https://img.example.com/3.jpg"
    assert ordered_images(row)[:2] == ["https://img.example.com/3.jpg", "https://img.example.com/0.jpg"]
    # 写真は URL のまま渡す（こちらからは取りに行かない）。8枚まで
    images = [b for b in llm.calls[0]["messages"][0]["content"] if b["type"] == "image"]
    assert len(images) == 8 and images[0]["source"]["type"] == "url"
    # 一度選んだものは選び直さない
    assert pick_pending(config, db, client=_hero_llm(0)) == (0, 0)


def test_no_exterior_keeps_original_order(config, db):
    aid = add_article(db)
    pick_pending(config, db, client=_hero_llm(-1))
    row = db.execute("SELECT * FROM articles WHERE id = ?", (aid,)).fetchone()
    assert row["hero_image"] == ""
    assert ordered_images(row) == json.loads(row["image_urls"])


def test_human_can_choose_the_first_photo(env):
    c, conn = env
    aid = add_article(conn)
    c.post(f"/articles/{aid}/hero", data={"url": "https://img.example.com/5.jpg"})
    assert conn.execute("SELECT hero_image FROM articles WHERE id = ?", (aid,)).fetchone()[0] \
        == "https://img.example.com/5.jpg"
    page = c.get(f"/articles/{aid}").text
    assert page.index("5.jpg") < page.index("0.jpg")
    r = c.post(f"/articles/{aid}/hero", data={"url": "https://other.example.com/x.jpg"},
               follow_redirects=False)
    assert "err=" in r.headers["location"]
