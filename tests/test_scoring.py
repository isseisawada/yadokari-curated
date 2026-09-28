from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from yadokari.db.repository import NewArticle, insert_article
from yadokari.scoring.client import FALLBACK_BETA, ScoringClient, ScoringError
from yadokari.scoring.prompt import SYSTEM_PROMPT
from yadokari.scoring.runner import score_pending
from yadokari.scoring.schema import FACT_FIELDS, OUTPUT_SCHEMA, Assessment
from yadokari.scoring.weights import build_axes, facts_score, freshness_score, photos_score, total


def _payload(**over) -> dict:
    data = {
        "relevant": True,
        "relevant_reason": "トレーラー型のタイニーハウス",
        "kind": "tiny_house_on_wheels",
        "facts": {k: "" for k in FACT_FIELDS} | {"builder": "Black Clay", "country": "オーストラリア", "area": "約20㎡"},
        "design_score": 80, "design_reason": "曲線の壁",
        "story_score": 70, "story_reason": "風景にひらく",
        "japan_score": 40, "japan_reason": "限られた空間",
        "summary_ja": "オーストラリアのビルダーによるタイニーハウス。",
        "highlights": ["全長8m"], "suggested_tags": ["タイニーハウス"],
    }
    data.update(over)
    return data


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _response(payload: dict | None = None, stop="end_turn"):
    content = [SimpleNamespace(type="thinking", thinking="")]
    if payload is not None:
        content.append(SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False)))
    return SimpleNamespace(stop_reason=stop, content=content)


def _client(config, *responses):
    messages = FakeMessages(responses)
    fake = SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages))
    return ScoringClient(config, SYSTEM_PROMPT, client=fake), messages


def test_schema_is_strict():
    assert OUTPUT_SCHEMA["additionalProperties"] is False
    assert set(OUTPUT_SCHEMA["required"]) == set(OUTPUT_SCHEMA["properties"])
    assert set(OUTPUT_SCHEMA["properties"]["facts"]["required"]) == set(FACT_FIELDS)


def test_assessment_clamps_and_blanks():
    a = Assessment.from_json(_payload(design_score=140, story_score=-3, kind="spaceship"))
    assert (a.design_score, a.story_score, a.kind) == (100, 0, "not_a_building")
    assert a.facts["price"] == ""


def test_request_uses_fallbacks_and_effort(config):
    client, messages = _client(config, _response(_payload()))
    client.assess("記事")
    call = messages.calls[0]
    assert call["betas"] == [FALLBACK_BETA]
    assert call["fallbacks"] == "default"
    assert call["output_config"]["effort"] == config.scoring.effort
    assert call["output_config"]["format"]["schema"] is OUTPUT_SCHEMA


def test_effort_is_not_sent_when_null(config):
    """Haiku 4.5 に effort を送ると 400 で全件落ちる（frmg で踏んだ）。"""
    config.scoring.effort = None
    config.scoring.fallbacks = False
    client, messages = _client(config, _response(_payload()))
    client.assess("記事")
    assert "effort" not in messages.calls[0]["output_config"]
    assert "fallbacks" not in messages.calls[0]


def test_refusal_and_truncation_are_errors(config):
    client, _ = _client(config, _response(stop="refusal"))
    with pytest.raises(ScoringError):
        client.assess("記事", max_attempts=1)
    client, _ = _client(config, _response(stop="max_tokens"))
    with pytest.raises(ScoringError):
        client.assess("記事", max_attempts=1)


def test_photos_facts_freshness_axes():
    assert photos_score(7)[0] == 100
    assert photos_score(4)[0] == 70
    assert photos_score(2)[0] == 30
    assert photos_score(0)[0] == 0
    assert facts_score({"architect": "x", "country": "日本", "area": "", "price": "$1"})[0] == 80
    now = datetime(2026, 9, 28, tzinfo=UTC)
    assert freshness_score((now - timedelta(days=3)).isoformat(), now)[0] == 100
    assert freshness_score((now - timedelta(days=200)).isoformat(), now)[0] == 20
    assert freshness_score(None, now)[0] == 50


def test_total_uses_config_weights(config):
    a = Assessment.from_json(_payload())
    now = datetime(2026, 9, 28, tzinfo=UTC)
    axes = build_axes(config.scoring.weights, a, 7, now.isoformat(), now)
    w = config.scoring.weights
    expected = (80 * w.design + 70 * w.story + 100 * w.smallness + 100 * w.photos + 40 * w.japan
                + 90 * w.facts + 100 * w.freshness + 100 * w.seo)
    assert total(axes, a) == round(expected, 1)


def test_irrelevant_scores_zero(config):
    a = Assessment.from_json(_payload(relevant=False, kind="not_a_building"))
    axes = build_axes(config.scoring.weights, a, 10, None)
    assert total(axes, a) == 0.0


def test_score_pending_saves_and_survives_failures(config, db):
    for u in ("https://t.com/1", "https://t.com/2"):
        insert_article(db, NewArticle(source="t", source_url=u, title="x", content_text="y", image_urls=["a"] * 7))
    db.commit()
    bad = ScoringError("400")
    client, _ = _client(config, _response(_payload()), bad)
    config.scoring.fallbacks = True
    stats = score_pending(config, db, client=client)
    assert (stats.scored, stats.failed) == (1, 1)
    rows = {r["source_url"]: r for r in db.execute("SELECT * FROM articles").fetchall()}
    ok, ng = rows["https://t.com/1"], rows["https://t.com/2"]
    assert ok["status"] == "scored" and ok["score"] > 0
    assert json.loads(ok["assessment"])["facts"]["builder"] == "Black Clay"
    assert ng["status"] == "collected" and ng["score_error"]


def test_trailer_scores_higher_than_cabin_of_same_quality(config):
    """「トレーラーハウス」で上位を狙う（2026-09-29）。同じ出来ならトレーラー系が上に来る。"""
    trailer = Assessment.from_json(_payload(kind="trailer_caravan"))
    cabin = Assessment.from_json(_payload(kind="cabin_hut"))
    w = config.scoring.weights
    assert total(build_axes(w, trailer, 7, None), trailer) > total(build_axes(w, cabin, 7, None), cabin)
