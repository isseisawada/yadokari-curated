from __future__ import annotations

import json

from helpers import SOURCE_TEXT, FakeLLM, add_article, draft_json

from yadokari.db.repository import decide, get_draft
from yadokari.drafting.generate import generate_for
from yadokari.drafting.numbers import plain, unsupported_numbers
from yadokari.drafting.render import DraftParts, build_body, build_title, via_domain


def test_numbers_not_in_source_are_found():
    src = "It is 400 sq ft and costs $140,000. Built in 2024."
    assert unsupported_numbers("400平方フィート、14万ドル。2024年完成。", src) == {"14"}
    # 単位換算で生まれた数字を拾う
    assert unsupported_numbers("約37㎡", src) == {"37"}
    # 1桁の言い回しは拾わないが、単位付きは拾う
    assert unsupported_numbers("2つの箱", src) == set()
    assert unsupported_numbers("3棟が並ぶ", src) == {"3"}
    # 全角数字も同じに扱う
    assert unsupported_numbers("２０２４年", src) == set()


def test_plain_strips_markdown_but_keeps_words_and_numbers():
    assert plain("**内と外**") == "内と外"
    assert plain("## 見出し") == "見出し"
    assert plain("- 20㎡の室内") == "20㎡の室内"


def test_title_follows_existing_format():
    parts = DraftParts.from_json(draft_json())
    assert build_title("【海外事例】", parts) == (
        "【海外事例】「小さく住む」を、ここまで心地よく。オーストラリア発のトレーラーハウス「Harper」"
    )


def test_body_matches_existing_layout():
    parts = DraftParts.from_json(draft_json())
    images = [f"https://img.example.com/{i}.jpg" for i in range(6)]
    html = build_body(parts, images, "https://www.blackclay.com.au/harper")
    # 写真から始まり、via はドメイン表記で元記事へのリンク
    assert html.startswith('<div class="wp-caption alignnone"><img')
    assert 'via: <a href="https://www.blackclay.com.au/harper">blackclay.com.au</a>' in html
    assert html.count("<h3><b>") == 2
    # マークダウンが本文に漏れない
    assert "**" not in html and "<p>- " not in html
    # 写真は全部使い、最後は出典の一覧
    assert html.count("wp-caption-text") == 6
    assert html.rstrip().endswith("</a></p>") and "via;<br />" in html


def test_html_in_llm_output_is_escaped():
    parts = DraftParts.from_json(draft_json(lead=["<script>alert(1)</script>"]))
    assert "<script>" not in build_body(parts, [], "https://a.com/x")


def test_via_domain_drops_www():
    assert via_domain("https://www.archdaily.com/1/x") == "archdaily.com"


def test_generate_retries_once_when_numbers_are_unsupported(config, db):
    article_id = add_article(db, status="approved")
    bad = draft_json(closing=["約215平方フィートの室内。"])
    llm = FakeLLM(bad, draft_json())
    draft_id = generate_for(config, db, article_id, client=llm)
    d = get_draft(db, draft_id)
    assert len(llm.calls) == 2
    assert "215" in llm.calls[1]["messages"][-1]["content"]
    assert json.loads(d["warnings"]) == []
    assert d["featured_image"] == "https://img.example.com/og.jpg"
    assert json.loads(d["tags"]) == ["タイニーハウス", "オーストラリア"]
    # 写真は max_images まで
    assert d["body_html"].count("wp-caption-text") == config.drafting.max_images


def test_generate_keeps_warning_when_retry_still_fails(config, db):
    article_id = add_article(db, status="approved")
    bad = draft_json(closing=["約215平方フィート。"])
    llm = FakeLLM(bad, bad)
    d = get_draft(db, generate_for(config, db, article_id, client=llm))
    assert json.loads(d["warnings"]) == ["元資料に無い数字: 215"]


def test_generate_uses_fallbacks_and_structured_output(config, db):
    article_id = add_article(db, status="approved")
    llm = FakeLLM(draft_json())
    generate_for(config, db, article_id, client=llm)
    call = llm.calls[0]
    assert call["fallbacks"] == "default"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert SOURCE_TEXT[:40] in call["messages"][0]["content"]


def test_only_approved_articles_get_drafts(config, db):
    article_id = add_article(db, status="scored")
    try:
        generate_for(config, db, article_id, client=FakeLLM(draft_json()))
    except ValueError:
        pass
    else:
        raise AssertionError("承認前の記事で下書きを作れてしまう")


def test_regenerating_keeps_one_draft_per_article(config, db):
    article_id = add_article(db, status="scored")
    decide(db, article_id, "approved")
    first = generate_for(config, db, article_id, client=FakeLLM(draft_json()))
    second = generate_for(config, db, article_id, client=FakeLLM(draft_json(name="Harper II")))
    assert first == second
    assert "Harper II" in get_draft(db, second)["title"]
