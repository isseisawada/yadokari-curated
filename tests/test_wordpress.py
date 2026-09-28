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
    assert post["tags"] == [274, 167]
    assert post["featured_media"] == wp.media[0]["id"]
    assert post["slug"].startswith("yc-")
    d = get_draft(db, draft_id)
    assert (d["state"], d["wp_post_id"]) == ("wp_draft", r.post_id)


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
    now = datetime(2026, 10, 1, 9, 45, tzinfo=UTC)  # JST 18:45（19:00 まで15分）
    first = next_free_slot(config, set(), now)
    assert first == datetime(2026, 10, 2, 10, 0, tzinfo=UTC)  # 翌日の 19:00 JST
    assert next_free_slot(config, {first.isoformat()}, now) == datetime(2026, 10, 3, 10, 0, tzinfo=UTC)


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
