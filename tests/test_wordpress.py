from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from helpers import FakeLLM, FakeWP, add_article, draft_json, fake_fetch

from yadokari.db.repository import edit_draft, get_draft
from yadokari.drafting.generate import generate_for
from yadokari.wordpress.publish import (
    ScheduleNotAllowed,
    next_free_slot,
    push,
    sync,
    to_wp_local,
)


def _draft(config, db, when: datetime | None = None) -> int:
    article_id = add_article(db, status="approved")
    draft_id = generate_for(config, db, article_id, client=FakeLLM(draft_json()))
    if when is not None:
        d = get_draft(db, draft_id)
        edit_draft(db, draft_id, title=d["title"], excerpt=d["excerpt"], body_html=d["body_html"],
                   tags=json.loads(d["tags"]), featured_image=d["featured_image"],
                   scheduled_at=when.isoformat())
        db.commit()
    return draft_id


def test_push_creates_a_wp_draft_with_category_tags_and_featured_image(config, db):
    wp = FakeWP()
    draft_id = _draft(config, db)
    r = push(config, db, draft_id, wp=wp.client(config), fetch=fake_fetch)
    post = wp.posts[r.post_id]
    assert post["status"] == "draft"
    assert post["categories"] == [2184]
    # 必須タグ（タイニーハウス・小屋・トレーラーハウス）は固定の ID、それ以外は検索で完全一致
    assert post["tags"] == [274, 162, 323, 167]
    assert post["featured_media"] == wp.media[0]["id"]
    assert post["slug"].startswith("yc-")
    d = get_draft(db, draft_id)
    assert (d["state"], d["wp_post_id"]) == ("wp_draft", r.post_id)
    # 記事分類 TINY HOUSE JOURNAL と QUOTE（mu-plugin の yc_journal / yc_quote が受け取る。2026-09-30）
    assert post["yc_journal"] is True
    assert post["yc_quote"] == "風景ごと住まいを考える、小さな家の誠実さ。"


def test_push_twice_updates_the_same_post(config, db):
    wp = FakeWP()
    draft_id = _draft(config, db)
    first = push(config, db, draft_id, wp=wp.client(config), fetch=fake_fetch)
    second = push(config, db, draft_id, wp=wp.client(config), fetch=fake_fetch)
    assert first.post_id == second.post_id
    assert len(wp.posts) == 1
    assert len(wp.media) == 1  # アイキャッチも二重に上げない


def test_lost_post_id_is_recovered_by_slug(config, db):
    """DB に post ID を書く前に落ちても、次の実行で2本目を作らない。"""
    wp = FakeWP()
    draft_id = _draft(config, db)
    push(config, db, draft_id, wp=wp.client(config), fetch=fake_fetch)
    db.execute("UPDATE drafts SET wp_post_id = NULL, wp_media_id = NULL WHERE id = ?", (draft_id,))
    db.commit()
    r = push(config, db, draft_id, wp=wp.client(config), fetch=fake_fetch)
    assert len(wp.posts) == 1
    assert r.notes and "同じ slug" in r.notes[0]


def test_schedule_is_refused_until_allowed(config, db):
    config.wordpress.allow_schedule = False
    wp = FakeWP()
    draft_id = _draft(config, db, datetime.now(UTC) + timedelta(days=1))
    assert config.wordpress.allow_schedule is False
    with pytest.raises(ScheduleNotAllowed):
        push(config, db, draft_id, schedule=True, wp=wp.client(config), fetch=fake_fetch)
    assert wp.posts == {}


def test_schedule_sends_future_with_local_date(config, db):
    config.wordpress.allow_schedule = True
    wp = FakeWP()
    when = datetime(2030, 1, 5, 10, 0, tzinfo=UTC)  # JST 19:00
    draft_id = _draft(config, db, when)
    r = push(config, db, draft_id, schedule=True, wp=wp.client(config), fetch=fake_fetch)
    post = wp.posts[r.post_id]
    assert post["status"] == "future"
    assert post["date"] == "2030-01-05T19:00:00"
    assert post["date_gmt"] == "2030-01-05T10:00:00"
    assert get_draft(db, draft_id)["state"] == "scheduled"


def test_schedule_in_the_past_is_refused(config, db):
    config.wordpress.allow_schedule = True
    draft_id = _draft(config, db, datetime.now(UTC) - timedelta(hours=1))
    with pytest.raises(ValueError):
        push(config, db, draft_id, schedule=True, wp=FakeWP().client(config), fetch=fake_fetch)


def test_published_post_is_never_overwritten(config, db):
    wp = FakeWP()
    draft_id = _draft(config, db)
    r = push(config, db, draft_id, wp=wp.client(config), fetch=fake_fetch)
    wp.posts[r.post_id]["status"] = "publish"
    with pytest.raises(ValueError):
        push(config, db, draft_id, wp=wp.client(config), fetch=fake_fetch)
    assert "公開済み" in get_draft(db, draft_id)["error"]


def test_featured_image_failure_does_not_stop_the_post(config, db):
    def broken(url):
        raise OSError("403")

    wp = FakeWP()
    r = push(config, db, _draft(config, db), wp=wp.client(config), fetch=broken)
    assert "featured_media" not in wp.posts[r.post_id]
    # 取り込めなければ元の URL のままアイキャッチに（mu-plugin → FIFU。2026-09-30）
    assert wp.posts[r.post_id]["yc_featured_url"].startswith("http")


def test_resend_keeps_schedule(config, db):
    from yadokari.wordpress.publish import resend

    config.wordpress.allow_schedule = True
    wp = FakeWP()
    draft_id = _draft(config, db, datetime.now(UTC) + timedelta(days=1))
    push(config, db, draft_id, schedule=True, wp=wp.client(config), fetch=fake_fetch)
    done = resend(config, db, wp=wp.client(config), fetch=fake_fetch)
    assert [x[0] for x in done] == [draft_id]
    assert next(iter(wp.posts.values()))["status"] == "future"


def test_sync_marks_published_and_reports_missed_schedules(config, db):
    config.wordpress.allow_schedule = True
    wp = FakeWP()
    now = datetime.now(UTC)
    a = _draft(config, db, now + timedelta(hours=2))
    ra = push(config, db, a, schedule=True, wp=wp.client(config), fetch=fake_fetch)
    b_article = add_article(db, url="https://t.com/b", status="approved")
    b = generate_for(config, db, b_article, client=FakeLLM(draft_json()))
    d = get_draft(db, b)
    edit_draft(db, b, title=d["title"], excerpt="", body_html=d["body_html"], tags=[],
               featured_image=None, scheduled_at=(now + timedelta(hours=1)).isoformat())
    db.commit()
    rb = push(config, db, b, schedule=True, wp=wp.client(config), fetch=fake_fetch)

    wp.posts[ra.post_id]["status"] = "publish"
    later = now + timedelta(hours=3)
    res = sync(config, db, wp=wp.client(config), now=later)
    assert [x[0] for x in res.published] == [a]
    assert [x[0] for x in res.missed] == [b]
    assert get_draft(db, a)["state"] == "published"
    assert get_draft(db, b)["wp_status"] == "future"
    assert rb.post_id != ra.post_id


def test_next_free_slot_skips_taken_and_too_soon(config):
    assert config.wordpress.post_times == ["08:00"]  # 毎日 AM8時（2026-09-30 ユーザー指定）
    now = datetime(2026, 9, 30, 22, 45, tzinfo=UTC)  # JST 10/1 07:45（08:00 まで15分）
    first = next_free_slot(config, set(), now)
    assert first == datetime(2026, 10, 1, 23, 0, tzinfo=UTC)  # 翌日 10/2 の 08:00 JST
    assert next_free_slot(config, {first.isoformat()}, now) == datetime(2026, 10, 2, 23, 0, tzinfo=UTC)


def test_more_post_times_means_more_posts_per_day(config):
    config.wordpress.post_times = ["07:00", "12:00", "19:00"]
    now = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)  # JST 09:00
    taken: set[str] = set()
    slots = []
    for _ in range(3):
        s = next_free_slot(config, taken, now)
        taken.add(s.isoformat())
        slots.append(s.astimezone().isoformat())
    assert len({s[:10] for s in slots}) <= 2


def test_to_wp_local(config):
    assert to_wp_local(config, "2026-10-01T10:00:00+00:00") == "2026-10-01T19:00:00"


def test_wp_check_reads_only(config, capsys, monkeypatch):
    """wp check は GET だけ。権限とカテゴリ・タグを確かめる。"""
    import httpx

    from yadokari import cli
    from yadokari.wordpress import client as wpc

    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        path = request.url.path
        if path.endswith("/users/me"):
            return httpx.Response(200, json={"name": "bot", "slug": "bot", "roles": ["editor"], "capabilities": {
                "edit_posts": True, "publish_posts": True, "upload_files": True}})
        if "/categories/" in path:
            return httpx.Response(200, json={"name": "タイニーハウス、最前線"})
        tid = int(path.rsplit("/", 1)[-1])
        names = {v: k for k, v in config.seo.tag_ids.items()}
        return httpx.Response(200, json={"name": names[tid]})

    real = wpc.WordPressClient.__init__

    def init(self, cfg, http=None):
        real(self, cfg, http=httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(wpc.WordPressClient, "__init__", init)
    monkeypatch.setenv("WP_USER", "bot")
    monkeypatch.setenv("WP_APP_PASSWORD", "abcd efgh ijkl mnop qrst uvwx")
    assert cli._wp_check(config) == 0
    assert set(methods) == {"GET"}
    out = capsys.readouterr().out
    assert "予約・公開する: OK" in out
    assert "英数字24文字" in out and "abcd" not in out  # 値は出さない


def test_credentials_also_go_in_x_yc_auth():
    """yadokari.net では Authorization が捨てられた（2026-09-30）。mu-plugin が X-YC-Auth を読む。"""
    import base64

    from yadokari.wordpress.client import auth_headers

    h = auth_headers("bot", "abcd efgh")
    assert h["X-YC-Auth"] == "Basic " + base64.b64encode(b"bot:abcd efgh").decode()


def test_archdaily_small_images_are_upgraded_to_large():
    from yadokari.collect.page import upgrade_image_url

    u = "https://images.adsttc.com/media/images/6808/1519/medium_jpg/koto_14.jpg?1745360181"
    assert upgrade_image_url(u) == "https://images.adsttc.com/media/images/6808/1519/large_jpg/koto_14.jpg?1745360181"
    assert upgrade_image_url(upgrade_image_url(u)) == upgrade_image_url(u)


def test_refresh_images_rewrites_and_repushes_keeping_schedule(config, db):
    from yadokari.wordpress.publish import refresh_images

    config.wordpress.allow_schedule = True
    wp = FakeWP()
    when = datetime.now(UTC) + timedelta(days=2)
    draft_id = _draft(config, db, when)
    small = "https://images.adsttc.com/media/images/1/medium_jpg/a.jpg?1"
    d = get_draft(db, draft_id)
    db.execute("UPDATE drafts SET body_html = ?, featured_image = ? WHERE id = ?",
               (d["body_html"] + f'<img src="{small}" />', small, draft_id))
    db.commit()
    push(config, db, draft_id, schedule=True, wp=wp.client(config), fetch=fake_fetch)
    media_before = len(wp.media)

    done = refresh_images(config, db, wp=wp.client(config), fetch=fake_fetch)
    assert [x[0] for x in done] == [draft_id]
    d = get_draft(db, draft_id)
    assert "medium_jpg" not in d["body_html"] and "large_jpg" in d["body_html"]
    assert "large_jpg" in d["featured_image"]
    post = wp.posts[d["wp_post_id"]]
    assert post["status"] == "future"
    assert len(wp.media) == media_before + 1  # アイキャッチを取り込み直した
    assert refresh_images(config, db, wp=wp.client(config), fetch=fake_fetch) == []
