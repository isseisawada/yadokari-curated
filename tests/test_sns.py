from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta

import httpx
from fastapi.testclient import TestClient
from helpers import FakeLLM, add_article

from yadokari.sns import official
from yadokari.web.app import create_app

# JST 10/13 10:00
NOW = datetime(2026, 10, 13, 1, 0, tzinfo=UTC)
BODY = "<p>広さ12平方メートルのトレーラーハウス。施主は「いちばん広い、広くない空間」と呼んだ。</p>" * 20


class FakeSnsWP:
    """WordPress の偽物（公式 SNS で使うところだけ）。"""

    def __init__(self):
        self.archive = [{"id": 500 + i, "link": f"https://yadokari.net/magazine/{500 + i}/",
                         "title": {"rendered": f"過去記事{i}"}, "content": {"rendered": BODY}}
                        for i in range(10)]
        self.queue: dict[int, dict] = {}

    def list_posts(self, params):
        if params.get("per_page") == 1:
            return self.archive[:1], len(self.archive)
        return list(self.archive), 1

    def sns_upsert(self, payload):
        if payload.get("cancel"):
            self.queue.pop(payload["key"], None)
        else:
            self.queue[payload["key"]] = payload
        return {"id": 1}

    def sns_status(self, keys):
        return {str(k): {"state": "posted", "x_id": f"x{k}", "threads_id": f"t{k}",
                         "posted_at": NOW.isoformat()} for k in keys if k in self.queue}

    def sns_metrics(self, ids):
        return {"posts": {i: {"views": 100, "likes": 3} for i in ids}, "followers": 50}

    def close(self):
        pass


def sns_config(config, until="2026-10-14"):
    config.sns.enabled = True
    config.sns.approval_until = until
    config.sns.lookahead_days = 1
    return config


def add_scheduled_draft(conn, publish_utc: datetime, wp_post_id=900) -> None:
    aid = add_article(conn, url=f"https://example.com/{wp_post_id}", status="approved")
    conn.execute(
        "INSERT INTO drafts (article_id, title, body_html, generated_at, state, scheduled_at, wp_post_id)"
        " VALUES (?, ?, ?, ?, 'scheduled', ?, ?)",
        (aid, "【海外事例】Shoji", BODY, NOW.isoformat(), publish_utc.isoformat(), wp_post_id),
    )
    conn.commit()


def good(text="広さ12平方メートルのトレーラーハウス。小さくても、何も欠けていない。", pattern="number"):
    return {"text": text, "pattern": pattern}


def test_plan_uses_new_article_at_new_slots_and_archive_otherwise(config, db):
    cfg = sns_config(config)
    # 10/13 16:00 JST に公開される記事 → 16:15 の枠
    add_scheduled_draft(db, datetime(2026, 10, 13, 7, 0, tzinfo=UTC))
    llm = FakeLLM(*[good() for _ in range(20)])
    res = official.plan(cfg, db, wp=FakeSnsWP(), client=llm, now=NOW, limit=20, rng=random.Random(1))
    rows = db.execute("SELECT * FROM sns_posts ORDER BY slot_at").fetchall()
    # 今日 12:15・16:15・20:15 ＋ 明日 4枠（08:15 は過ぎている）
    assert [r["slot_at"] for r in rows][:3] == [
        datetime(2026, 10, 13, 3, 15, tzinfo=UTC).isoformat(),
        datetime(2026, 10, 13, 7, 15, tzinfo=UTC).isoformat(),
        datetime(2026, 10, 13, 11, 15, tzinfo=UTC).isoformat()]
    assert len(rows) == 7 and not res.failed
    new = rows[1]
    assert new["kind"] == "new" and new["wp_post_id"] == 900
    assert new["link"] == "https://yadokari.net/magazine/900/"
    # 新着の無い枠（明日の 08:15・16:15）は過去記事で埋める
    assert sum(r["kind"] == "archive" for r in rows) == 6
    # 同じ過去記事は使わない
    archive_ids = [r["wp_post_id"] for r in rows if r["kind"] == "archive"]
    assert len(set(archive_ids)) == len(archive_ids)
    # 承認期間（10/14 まで）は承認待ち
    assert {r["state"] for r in rows} == {"draft"}


def test_after_approval_period_goes_straight_to_approved(config, db):
    cfg = sns_config(config, until="2026-10-01")
    official.plan(cfg, db, wp=FakeSnsWP(), client=FakeLLM(*[good() for _ in range(8)]), now=NOW, limit=2)
    assert {r["state"] for r in db.execute("SELECT state FROM sns_posts").fetchall()} == {"approved"}


def test_numbers_not_in_the_article_are_held(config, db):
    cfg = sns_config(config, until=None)
    bad = good(text="広さ15平方メートルのトレーラーハウス。小さくても、何も欠けていない。")
    llm = FakeLLM(bad, bad)
    official.plan(cfg, db, wp=FakeSnsWP(), client=llm, now=NOW, limit=1)
    row = db.execute("SELECT * FROM sns_posts").fetchone()
    assert row["state"] == "draft"  # 承認期間外でも人が直す
    assert "15" in json.loads(row["warnings"])[0]
    assert "15" in llm.calls[1]["messages"][-1]["content"]  # 1回は書き直させた


def test_push_tags_link_and_sync_reads_results(config, db):
    cfg = sns_config(config, until=None)
    wp = FakeSnsWP()
    official.plan(cfg, db, wp=wp, client=FakeLLM(good()), now=NOW, limit=1)
    n, errors = official.push(cfg, db, wp, now=NOW)
    assert n == 1 and not errors
    sent = next(iter(wp.queue.values()))
    assert sent["url"].endswith("?utm_medium=social&utm_campaign=yd_sns_archive")
    assert sent["at"] == datetime(2026, 10, 13, 3, 15, tzinfo=UTC).isoformat()
    assert official.sync(db, wp) == 1
    row = db.execute("SELECT * FROM sns_posts").fetchone()
    assert (row["state"], row["x_id"], row["threads_id"]) == ("posted", f"x{row['id']}", f"t{row['id']}")


def test_measure_and_hints(config, db):
    for i, (pattern, views) in enumerate([("number", 900)] * 3 + [("question", 100)] * 3):
        db.execute(
            "INSERT INTO sns_posts (kind, slot_at, wp_post_id, link, text, pattern, state, created_at, metrics)"
            " VALUES ('archive', ?, ?, 'u', 't', ?, 'posted', ?, ?)",
            ((NOW - timedelta(days=5, hours=i)).isoformat(), i, pattern, NOW.isoformat(),
             json.dumps({"x": {"impressions": views}})))
    db.commit()
    h = official.hints(db, NOW)
    assert h.startswith("最近よく読まれた型: 数字から入る（平均900表示）") and "伸びなかった型: 問いから入る" in h

    db.execute("UPDATE sns_posts SET x_id = 'x' || id, threads_id = 't' || id")
    db.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/2/users/me":
            return httpx.Response(200, json={"data": {"public_metrics": {"followers_count": 3934}}})
        ids = request.url.params["ids"].split(",")
        return httpx.Response(200, json={"data": [
            {"id": i, "public_metrics": {"like_count": 2, "impression_count": 300},
             "non_public_metrics": {"impression_count": 310, "url_link_clicks": 4}} for i in ids]})

    keys = {"api_key": "a", "api_secret": "b", "access_token": "c", "access_secret": "d"}
    assert official.measure_due(db, NOW)
    out = official.measure(db, FakeSnsWP(), keys, now=NOW, http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert out["errors"] == [] and out["posts"] == 6
    m = json.loads(db.execute("SELECT metrics FROM sns_posts LIMIT 1").fetchone()["metrics"])
    assert m["x"]["impressions"] == 310 and m["x"]["clicks"] == 4 and m["threads"]["views"] == 100
    assert not official.measure_due(db, NOW)
    assert "フォロワー3934" in official.weekly_summary(db, NOW)


def test_review_page_edit_approve_and_skip(config, db, tmp_path):
    cfg = sns_config(config)
    cfg.app.db_path = str(tmp_path / "t.db")
    wp = FakeSnsWP()
    official.plan(cfg, db, wp=wp, client=FakeLLM(*[good() for _ in range(3)]),
                  now=datetime.now(UTC), limit=3)
    client = TestClient(create_app(cfg, wp_factory=lambda: wp))
    page = client.get("/sns")
    assert page.status_code == 200 and "承認待ち 3 本をすべて承認" in page.text
    ids = [r["id"] for r in db.execute("SELECT id FROM sns_posts ORDER BY slot_at").fetchall()]
    r = client.post(f"/sns/{ids[0]}", data={"text": "書き直した文面。", "do": "approve"}, follow_redirects=False)
    assert r.status_code == 303
    row = db.execute("SELECT text, state FROM sns_posts WHERE id = ?", (ids[0],)).fetchone()
    assert (row["text"], row["state"]) == ("書き直した文面。", "approved")
    too_long = "あ" * 200
    assert "err=" in client.post(f"/sns/{ids[1]}", data={"text": too_long}, follow_redirects=False).headers["location"]
    client.post(f"/sns/{ids[1]}/skip")
    assert db.execute("SELECT state FROM sns_posts WHERE id = ?", (ids[1],)).fetchone()["state"] == "skipped"
    client.post("/sns/approve-all")
    assert db.execute("SELECT state FROM sns_posts WHERE id = ?", (ids[2],)).fetchone()["state"] == "approved"
    client.post(f"/sns/{ids[2]}/redo")
    assert db.execute("SELECT 1 FROM sns_posts WHERE id = ?", (ids[2],)).fetchone() is None


def test_x_get_signs_query():
    from yadokari.sns.x import _oauth_header

    keys = {"api_key": "a", "api_secret": "b", "access_token": "c", "access_secret": "d"}
    a = _oauth_header("GET", "https://api.x.com/2/tweets", keys, {"ids": "1"})
    assert a.startswith("OAuth ") and "ids" not in a


def test_clear_drafts_keeps_approved(config, db):
    cfg = sns_config(config)
    official.plan(cfg, db, wp=FakeSnsWP(), client=FakeLLM(*[good() for _ in range(3)]), now=NOW, limit=3)
    first = db.execute("SELECT id FROM sns_posts ORDER BY slot_at").fetchone()["id"]
    db.execute("UPDATE sns_posts SET state = 'approved' WHERE id = ?", (first,))
    db.commit()
    assert official.clear_drafts(db, NOW) == 2
    assert [r["id"] for r in db.execute("SELECT id FROM sns_posts").fetchall()] == [first]


def test_archive_prefers_interviews_and_skips_excluded_and_unusable(config, db):
    cfg = sns_config(config, until=None)

    class PoolWP(FakeSnsWP):
        def __init__(self):
            super().__init__()
            self.seen = []

        def list_posts(self, params):
            self.seen.append(params.get("search"))
            if params.get("search") == "インタビュー":
                posts = [
                    {"id": 1, "link": "l1", "title": {"rendered": "晴耕雨読の家・那須・430万円"},
                     "content": {"rendered": BODY}},
                    {"id": 2, "link": "l2", "title": {"rendered": "【インタビュー】終わった募集の話"},
                     "content": {"rendered": BODY}},
                    {"id": 3, "link": "l3", "title": {"rendered": "【インタビュー】小さな家に暮らす人"},
                     "content": {"rendered": BODY}},
                ]
                return (posts[:1], 3) if params.get("per_page") == 1 else (posts, 1)
            return super().list_posts(params)

    wp = PoolWP()
    unusable = {**good(), "usable": False, "reason": "募集は終わっている"}
    # 1本目（id 2 か 3）を AI が外したら、もう1本を使う
    llm = FakeLLM(unusable, {**good(), "usable": True, "reason": "ok"})
    official.plan(cfg, db, wp=wp, client=llm, now=NOW, limit=1, rng=random.Random(0))
    row = db.execute("SELECT * FROM sns_posts").fetchone()
    assert row["kind"] == "archive" and row["wp_post_id"] in (2, 3)
    assert wp.seen[0] == "インタビュー"
    assert len(llm.calls) == 2  # 430万円の物件は AI に見せる前に外れる
    first_title = llm.calls[0]["messages"][0]["content"]
    assert "430万円" not in first_title


def test_human_edits_become_examples_and_ai_words_are_retried(config, db):
    cfg = sns_config(config, until=None)
    official.plan(cfg, db, wp=FakeSnsWP(), client=FakeLLM(good()), now=NOW, limit=1)
    db.execute("UPDATE sns_posts SET text = '人が直した文。いいなあ。', state = 'approved'")
    db.commit()
    assert official.edits(db) == [(good()["text"], "人が直した文。いいなあ。")]
    ai_ish = good(text="広さ12平方メートルのトレーラーハウス。暮らしの余白が豊かに広がる。")
    llm = FakeLLM(ai_ish, good())
    official.plan(cfg, db, wp=FakeSnsWP(), client=llm, now=NOW, limit=1)
    first = llm.calls[0]["messages"][0]["content"]
    assert "編集部が AI の案をこう直した" in first and "人が直した文。いいなあ。" in first
    assert "余白" in llm.calls[1]["messages"][-1]["content"]  # AIっぽい言葉は1回書き直させる
    row = db.execute("SELECT * FROM sns_posts ORDER BY id DESC").fetchone()
    assert row["text"] == good()["text"] and row["ai_text"] == good()["text"]


def test_archive_after_is_sent(config, db):
    cfg = sns_config(config, until=None)
    assert cfg.sns.archive_after == "2020-01-01"
    seen = []

    class AfterWP(FakeSnsWP):
        def list_posts(self, params):
            seen.append(params.get("after"))
            return super().list_posts(params)

    official.plan(cfg, db, wp=AfterWP(), client=FakeLLM(good()), now=NOW, limit=1)
    assert seen and set(seen) == {"2020-01-01T00:00:00"}
