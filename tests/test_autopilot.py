from __future__ import annotations

from datetime import UTC, datetime

from helpers import FakeLLM, FakeWP, add_article, draft_json, fake_fetch

from yadokari import autopilot
from yadokari.db.repository import decide, get_draft, get_draft_by_article
from yadokari.drafting.generate import generate_for

NOW = datetime(2026, 9, 30, 2, 0, tzinfo=UTC)  # JST 11:00


def _on(config):
    config.wordpress.allow_schedule = True
    config.wordpress.auto_schedule = True


def _approved(db, url):
    aid = add_article(db, url=url)
    decide(db, aid, "approved")
    db.commit()
    return aid


def test_approved_articles_get_drafted_and_scheduled_in_the_next_free_slots(config, db):
    _on(config)
    wp = FakeWP()
    a = _approved(db, "https://t.com/a")
    b = _approved(db, "https://t.com/b")
    res = autopilot.run(config, db, client=FakeLLM(draft_json(), draft_json()),
                        wp=wp.client(config), fetch=fake_fetch, now=NOW)
    assert len(res.generated) == 2 and not res.failed
    da, db_ = get_draft_by_article(db, a), get_draft_by_article(db, b)
    # 承認の古い順に、毎日 08:00 JST（= 前日 23:00 UTC）の空いている最短の枠
    assert da["scheduled_at"] == "2026-09-30T23:00:00+00:00"
    assert db_["scheduled_at"] == "2026-10-01T23:00:00+00:00"
    assert {p["status"] for p in wp.posts.values()} == {"future"}


def test_existing_unscheduled_draft_is_scheduled_without_regenerating(config, db):
    _on(config)
    wp = FakeWP()
    aid = _approved(db, "https://t.com/a")
    draft_id = generate_for(config, db, aid, client=FakeLLM(draft_json()))
    llm = FakeLLM()
    res = autopilot.run(config, db, client=llm, wp=wp.client(config), fetch=fake_fetch, now=NOW)
    assert [d for d, _ in res.scheduled] == [draft_id]
    assert llm.calls == []


def test_limit_caps_new_drafts_per_run(config, db):
    _on(config)
    for i in range(3):
        _approved(db, f"https://t.com/{i}")
    res = autopilot.run(config, db, limit=1, client=FakeLLM(draft_json()),
                        wp=FakeWP().client(config), fetch=fake_fetch, now=NOW)
    assert len(res.generated) == 1


def test_draft_with_unsupported_numbers_is_held_for_a_human(config, db):
    _on(config)
    bad = draft_json()
    bad["closing"] = ["価格は999万円。"]
    wp = FakeWP()
    _approved(db, "https://t.com/a")
    res = autopilot.run(config, db, client=FakeLLM(bad, bad), wp=wp.client(config),
                        fetch=fake_fetch, now=NOW)
    assert res.held and not res.scheduled
    assert wp.posts == {}


def test_failed_push_frees_the_slot(config, db):
    _on(config)
    aid = _approved(db, "https://t.com/a")
    draft_id = generate_for(config, db, aid, client=FakeLLM(draft_json()))
    config.wordpress.post_times = ["08:00"]
    wp = FakeWP()

    class Broken:
        def __getattr__(self, name):
            raise RuntimeError("WP が落ちている")

        def close(self):
            pass

    res = autopilot.run(config, db, wp=Broken(), fetch=fake_fetch, now=NOW)
    assert res.failed
    assert get_draft(db, draft_id)["scheduled_at"] is None
    assert wp.posts == {}


def test_nothing_happens_when_turned_off(config, db):
    config.wordpress.auto_schedule = False
    _approved(db, "https://t.com/a")
    llm = FakeLLM()
    res = autopilot.run(config, db, client=llm, wp=FakeWP().client(config), now=NOW)
    assert (res.generated, res.scheduled, llm.calls) == ([], [], [])
