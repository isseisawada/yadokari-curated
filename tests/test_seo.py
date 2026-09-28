from __future__ import annotations

import json

from helpers import SOURCE_TEXT, FakeLLM, FakeWP, add_article, draft_json, fake_fetch

from yadokari.db.repository import get_draft
from yadokari.drafting.generate import checked_facts, generate_for
from yadokari.drafting.render import DraftParts, build_body
from yadokari.drafting.seo import checks
from yadokari.wordpress.publish import push


def _set_kind(db, article_id, kind):
    row = db.execute("SELECT assessment FROM articles WHERE id = ?", (article_id,)).fetchone()
    a = json.loads(row["assessment"])
    a["kind"] = kind
    db.execute("UPDATE articles SET assessment = ? WHERE id = ?", (json.dumps(a, ensure_ascii=False), article_id))
    db.commit()


def test_every_draft_gets_tiny_house_and_koya_tags(config, db):
    aid = add_article(db, status="approved")
    _set_kind(db, aid, "cabin_hut")
    d = get_draft(db, generate_for(config, db, aid, client=FakeLLM(draft_json())))
    tags = json.loads(d["tags"])
    assert tags[:2] == ["タイニーハウス", "小屋"]
    assert "トレーラーハウス" not in tags  # トレーラーでないものには付けない


def test_trailer_gets_all_three_tags_and_trailer_keyword(config, db):
    aid = add_article(db, status="approved")
    _set_kind(db, aid, "trailer_caravan")
    llm = FakeLLM(draft_json())
    d = get_draft(db, generate_for(config, db, aid, client=llm))
    assert json.loads(d["tags"])[:3] == ["タイニーハウス", "小屋", "トレーラーハウス"]
    assert "主キーワード: トレーラーハウス" in llm.calls[0]["messages"][0]["content"]
    assert "トレーラーハウスの記事一覧" in d["body_html"]


def test_keyword_by_kind(config):
    assert config.seo.keyword_for("tiny_house_on_wheels") == "トレーラーハウス"
    assert config.seo.keyword_for("cabin_hut") == "小屋"
    assert config.seo.keyword_for("container") == "タイニーハウス"


def test_excerpt_is_the_seo_description(config, db):
    aid = add_article(db, status="approved")
    d = get_draft(db, generate_for(config, db, aid, client=FakeLLM(draft_json())))
    assert d["excerpt"].startswith("オーストラリアのビルダーBlack Clayが手がけたトレーラーハウス")


def test_alt_data_box_and_faq(config):
    parts = DraftParts.from_json(draft_json())
    html = build_body(parts, ["https://i/1.jpg", "https://i/2.jpg"], "https://a.com/x",
                      facts={"name": "Harper", "country": "オーストラリア", "price": ""},
                      internal_links=config.seo.internal_links, link_keywords=["トレーラーハウス"])
    assert 'alt="オーストラリア発のトレーラーハウス「Harper」の写真 1/2"' in html
    assert "<h3><b>Harperのデータ</b></h3>" in html and "<li>国：オーストラリア</li>" in html
    assert "価格：" not in html  # 空の項目は行ごと出さない
    assert "Q. Harperの広さは？" in html


def test_data_box_drops_facts_with_numbers_not_in_source():
    facts, dropped = checked_facts({"area": "約20㎡", "price": "$99,000", "country": "オーストラリア"},
                                   SOURCE_TEXT)
    assert "price" not in facts and "area" in facts
    assert dropped and "99" in dropped[0]


def test_checks_pass_on_a_well_formed_trailer_draft(config):
    parts = DraftParts.from_json(draft_json(
        lead=["「Harper」は、オーストラリアのBlack Clayが手がけたトレーラーハウスだ。" + "暮らしの工夫が詰まっている。" * 3,
              "トレーラーハウスの質的進化を体現する。" + "風景にひらく。" * 20],
        sections=[{"heading": "トレーラーハウスに込められた誠実さ", "paragraphs": ["小さな家の工夫。" * 100]},
                  {"heading": "内と外", "paragraphs": ["軽やかな暮らし。" * 30]}],
    ))
    html = build_body(parts, ["https://i/1.jpg"], "https://a.com/x", facts={"name": "Harper"},
                      internal_links=config.seo.internal_links, link_keywords=["トレーラーハウス"])
    results = checks(config.seo, title="【海外事例】心地よく。オーストラリア発のトレーラーハウス「Harper」",
                     excerpt=parts.description, body_html=html,
                     tags=["タイニーハウス", "小屋", "トレーラーハウス"], keyword="トレーラーハウス")
    assert [c.label for c in results if not c.ok] == []


def test_checks_flag_missing_keyword_and_tags(config):
    results = checks(config.seo, title="【海外事例】森の家「X」", excerpt="短い", body_html="<p>本文</p>",
                     tags=["自然"], keyword="トレーラーハウス")
    failed = {c.label for c in results if not c.ok}
    assert "タイトルに「トレーラーハウス」" in failed
    assert any(label.startswith("必須タグ") for label in failed)


def test_push_uses_fixed_tag_ids(config, db):
    wp = FakeWP(tags={})  # 検索では何も見つからない状態
    aid = add_article(db, status="approved")
    draft_id = generate_for(config, db, aid, client=FakeLLM(draft_json()))
    r = push(config, db, draft_id, wp=wp.client(config), fetch=fake_fetch)
    assert wp.posts[r.post_id]["tags"][:3] == [274, 162, 323]
