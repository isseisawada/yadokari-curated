from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from helpers import FakeLLM, assessment
from test_collect import FakeClient

from yadokari.collect.manual import add_manual
from yadokari.collect.runner import collect_all, collect_source
from yadokari.db.connection import connect
from yadokari.db.migrate import migrate
from yadokari.scoring.client import ScoringClient
from yadokari.scoring.prompt import SYSTEM_PROMPT
from yadokari.web.app import create_app

PROHIBITED = {"archdaily", "dwell", "gessato"}


def test_sources_whose_terms_forbid_crawling_are_manual_only(config):
    """2026-09-28 に利用規約で自動収集の禁止を確認したもの。"""
    for name in PROHIBITED:
        s = config.source(name)
        assert s.manual_only and not s.feed and not s.index_urls


def test_manual_only_source_is_never_fetched_even_when_named(config, db):
    client = FakeClient({})
    stats = collect_source(config, client, db, config.source("archdaily"))
    assert stats.inserted == 0
    assert client.requested == []


def test_collect_all_skips_manual_only(config, monkeypatch):
    seen = []
    monkeypatch.setattr("yadokari.collect.runner.collect_source",
                        lambda cfg, client, conn, source, **kw: seen.append(source.name) or
                        type("S", (), {"summary": lambda self: ""})())
    collect_all(config, None, dry_run=True)
    assert not (set(seen) & PROHIBITED)


def test_add_manual_stores_human_input(config, db):
    res = add_manual(config, db, source="archdaily", url="https://www.archdaily.com/1/x/",
                     title="Cabin", text="A cabin  in the woods.",
                     image_urls=["https://images.adsttc.com/1.jpg", "", "not a url",
                                 "https://images.adsttc.com/2.jpg"], credit="© Someone")
    row = db.execute("SELECT * FROM articles WHERE id = ?", (res.article_id,)).fetchone()
    assert row["source_url"] == "https://www.archdaily.com/1/x"
    assert json.loads(row["image_urls"]) == ["https://images.adsttc.com/1.jpg", "https://images.adsttc.com/2.jpg"]
    assert row["og_image"] == "https://images.adsttc.com/1.jpg"
    again = add_manual(config, db, source="archdaily", url="https://www.archdaily.com/1/x",
                       title="Cabin", text="x", image_urls=[])
    assert again.duplicate


def test_add_manual_refuses_auto_sources_and_missing_text(config, db):
    with pytest.raises(ValueError):
        add_manual(config, db, source="tinyhousetalk", url="https://a.com/x", title="t", text="x", image_urls=[])
    with pytest.raises(ValueError):
        add_manual(config, db, source="archdaily", url="https://a.com/x", title="t", text=" ", image_urls=[])


def test_manual_page_then_score(config, tmp_path):
    path = tmp_path / "m.db"
    migrate(path)
    config.app.db_path = str(path)
    config.scoring.fallbacks = False
    scorer = ScoringClient(config, SYSTEM_PROMPT, client=FakeLLM(assessment()))
    c = TestClient(create_app(config, scorer=scorer))
    assert "archdaily" in c.get("/manual").text
    r = c.post("/manual", data={"source": "archdaily", "url": "https://www.archdaily.com/9/y",
                                "title": "Cabin", "text": "A cabin.", "images": "https://i/1.jpg"},
               follow_redirects=False)
    path_ = r.headers["location"].split("?")[0]
    r = c.post(f"{path_}/score", follow_redirects=False)
    assert "msg=" in r.headers["location"]
    conn = connect(path)
    assert conn.execute("SELECT status FROM articles").fetchone()["status"] == "scored"
    conn.close()
