from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from helpers import FakeLLM, add_article

from yadokari.db.repository import approved_rules, decide, list_rule_candidates, set_rule_state
from yadokari.learning.loop import LearningClient, run_learning
from yadokari.monitor import check


def _reject(db, n: int, reason: str, tag: str | None = None):
    for i in range(n):
        aid = add_article(db, url=f"https://t.com/{reason}/{i}/{tag}")
        decide(db, aid, "rejected", reason=reason, tag=tag)
    db.commit()


def test_rules_are_proposed_after_three_hits_and_only_approved_ones_count(config, db):
    tag = config.learning.tags[0]
    _reject(db, 3, "邸宅で小さくない")
    llm = FakeLLM(
        {"assignments": [{"id": i, "tag": tag} for i in (1, 2, 3)]},
        {"proposal": "延床が大きい邸宅は relevant を false にする"},
    )
    stats = run_learning(config, db, client=LearningClient(config, client=llm))
    assert stats.tagged == 3
    assert len(stats.new_candidates) == 1
    assert approved_rules(db) == []  # 候補のままでは採点に効かない
    rule = list_rule_candidates(db)[0]
    set_rule_state(db, rule["id"], "approved")
    assert approved_rules(db) == ["延床が大きい邸宅は relevant を false にする"]


def test_human_selected_tags_skip_llm_classification(config, db):
    tag = config.learning.tags[1]
    _reject(db, 2, "デザインが普通", tag=tag)
    llm = FakeLLM()
    stats = run_learning(config, db, client=LearningClient(config, client=llm))
    assert stats.tagged == 0
    assert llm.calls == []  # 2件なので候補も出ない


def test_dismissed_rules_are_not_proposed_again(config, db):
    tag = config.learning.tags[2]
    _reject(db, 3, "写真が2枚", tag=tag)
    llm = FakeLLM({"proposal": "写真が3枚以下は減点"}, {"proposal": "別案"})
    run_learning(config, db, client=LearningClient(config, client=llm))
    rule = list_rule_candidates(db)[0]
    set_rule_state(db, rule["id"], "dismissed")
    db.commit()
    stats = run_learning(config, db, client=LearningClient(config, client=llm))
    assert stats.new_candidates == []
    assert list_rule_candidates(db)[0]["state"] == "dismissed"


def _wp(posts):
    def handler(request):
        return httpx.Response(200, json=posts)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_monitor_flags_scheduled_post_not_published(config, db):
    aid = add_article(db, status="approved")
    now = datetime.now(UTC)
    db.execute(
        "INSERT INTO drafts (article_id, title, body_html, generated_at, state, scheduled_at, wp_post_id, wp_status)"
        " VALUES (?, 't', 'b', ?, 'scheduled', ?, 1, 'future')",
        (aid, now.isoformat(), (now - timedelta(hours=1)).isoformat()),
    )
    db.commit()
    report = check(config, db, now=now, http=_wp([]))
    assert not report.ok
    assert any("公開を確認できていません" in line for line in report.lines)


def test_monitor_ok_when_nothing_expected(config, db):
    report = check(config, db, http=_wp([{"id": 1, "date": "2026-09-28T19:00:00", "link": "x"}]))
    assert report.ok


def test_monitor_flags_unreachable_wp(config):
    def handler(request):
        return httpx.Response(503)

    report = check(config, None, http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert not report.ok
