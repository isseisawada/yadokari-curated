from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient
from helpers import FakeLLM, FakeWP, add_article, draft_json, fake_fetch

from yadokari.db.connection import connect
from yadokari.db.migrate import migrate
from yadokari.web.app import create_app
from yadokari.web.auth import BasicAuth


@pytest.fixture
def env(config, tmp_path):
    path = tmp_path / "w.db"
    migrate(path)
    config.app.db_path = str(path)
    wp = FakeWP()
    llm = FakeLLM(*[draft_json() for _ in range(5)])
    app = create_app(config, drafter=llm, wp_factory=lambda: wp.client(config), fetch=fake_fetch)
    conn = connect(path)
    yield TestClient(app), conn, wp
    conn.close()


def test_healthz_does_not_need_auth(config):
    app = create_app(config, auth=BasicAuth("u", "p"))
    c = TestClient(app)
    assert c.get("/healthz").status_code == 200
    assert c.get("/articles").status_code == 401
    token = base64.b64encode(b"u:p").decode()
    assert c.get("/healthz", headers={"Authorization": f"Basic {token}"}).status_code == 200


def test_list_shows_scored_articles_over_threshold(env):
    c, conn, _ = env
    add_article(conn, url="https://t.com/hi", score=80)
    add_article(conn, url="https://t.com/lo", score=10)
    html = c.get("/articles").text
    assert "Harper tiny house" in html
    assert html.count('class="card"') == 1


def test_reject_needs_a_reason(env):
    c, conn, _ = env
    aid = add_article(conn)
    r = c.post(f"/articles/{aid}/decide", data={"decision": "rejected"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    assert conn.execute("SELECT status FROM articles WHERE id = ?", (aid,)).fetchone()["status"] == "scored"


def test_approve_draft_edit_and_push_flow(env):
    c, conn, wp = env
    aid = add_article(conn)
    c.post(f"/articles/{aid}/decide", data={"decision": "approved"})
    r = c.post(f"/articles/{aid}/draft", follow_redirects=False)
    draft_path = r.headers["location"].split("?")[0]
    assert draft_path.startswith("/drafts/")
    page = c.get(draft_path).text
    assert "【海外事例】" in page and "wp-caption" in page
    assert "SEO / AIO" in page and "主キーワード「トレーラーハウス」" in page
    # 予約ボタンは押せる（allow_schedule: true。2026-09-30 有効化）
    assert "予約はまだ無効です" not in page

    c.post(draft_path, data={"title": "直したタイトル", "excerpt": "抜粋", "body_html": "<p>本文</p>",
                             "tags": "タイニーハウス、オーストラリア", "featured_image": "",
                             "scheduled_at": "2030-01-05T19:00"})
    row = conn.execute("SELECT * FROM drafts").fetchone()
    assert row["title"] == "直したタイトル"
    assert row["scheduled_at"] == "2030-01-05T10:00:00+00:00"

    r = c.post(f"{draft_path}/push", data={"mode": "draft"}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    assert next(iter(wp.posts.values()))["status"] == "draft"

    r = c.post(f"{draft_path}/push", data={"mode": "schedule"}, follow_redirects=False)
    assert "msg=" in r.headers["location"]
    post = next(iter(wp.posts.values()))
    assert post["status"] == "future"
    assert post["date"] == "2030-01-05T19:00:00"


def test_rules_page_and_approval(env):
    c, conn, _ = env
    conn.execute("INSERT INTO rule_candidates (reason_tag, hit_count, proposal, created_at)"
                 " VALUES ('x', 3, 'ルール案', '2026-09-28')")
    conn.commit()
    assert "ルール案" in c.get("/rules").text
    c.post("/rules/1", data={"state": "approved", "proposal": "直したルール"})
    row = conn.execute("SELECT * FROM rule_candidates").fetchone()
    assert (row["state"], row["proposal"]) == ("approved", "直したルール")


def test_bulk_approve_and_reject(env):
    """一覧でチェックしたものをまとめて承認・非承認（2026-10-03）。非承認はタグが要る。"""
    c, conn, _ = env
    a = add_article(conn, url="https://t.com/1", score=80)
    b = add_article(conn, url="https://t.com/2", score=80)
    d = add_article(conn, url="https://t.com/3", score=80)
    html = c.get("/articles").text
    assert 'name="ids"' in html and "まとめて承認" in html

    r = c.post("/articles/bulk", data={"ids": [str(a), str(b)], "decision": "approved"},
               headers={"X-Requested-With": "fetch"})
    assert r.json()["ok"] and sorted(r.json()["done"]) == sorted([a, b])
    st = {row["id"]: row["status"] for row in conn.execute("SELECT id, status FROM articles")}
    assert st[a] == st[b] == "approved" and st[d] == "scored"

    r = c.post("/articles/bulk", data={"ids": [str(d)], "decision": "rejected"},
               headers={"X-Requested-With": "fetch"})
    assert r.status_code == 400  # 理由のタグが無い
    r = c.post("/articles/bulk", data={"ids": [str(d)], "decision": "rejected", "tag": "デザインが弱い"},
               headers={"X-Requested-With": "fetch"})
    assert r.json()["done"] == [d]
    row = conn.execute("SELECT status FROM articles WHERE id = ?", (d,)).fetchone()
    assert row["status"] == "rejected"


def test_select_all_is_hidden_when_nothing_to_review(env):
    c, conn, _ = env
    assert 'id="bulk-all"' not in c.get("/articles").text
    add_article(conn, url="https://t.com/x", score=80)
    assert 'id="bulk-all"' in c.get("/articles").text
